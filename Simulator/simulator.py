#!/usr/bin/env python3
"""
EV-Pulse BMW Playback Simulator  v2
────────────────────────────────────────────────────────────────
Role: compute raw sensor values + derived variables in real time
      and stream them to Azure IoT Hub.

Pipeline:
  Simulator  (raw sensors + derived variables)
    -> IoT Hub
    -> Stream Analytics  (column routing)
    -> Azure ML          (Z-score · BSI · Normal / Warning / Critical)
    -> Azure SQL         (ML results + location + vehicle metadata)
    -> HTML dashboard

Derived variables calculated by this simulator (per BSI methodology):
  Delta_I              = I(t) - I(t-1)       [current step change, A]
  Delta_V              = V(t) - V(t-1)       [voltage step change, V]
  Joule_Heating_Stress = I(t)^2 * T(t)       [Joule heating stress, A^2·°C]

NOT calculated here (Azure ML responsibility):
  Z_Delta_I / Z_Delta_V / Z_Thermal_Stress
  Z_Battery_Current / Z_Battery_Voltage / Z_BSI
  BSI / status (Normal · Warning · Critical)
────────────────────────────────────────────────────────────────
"""

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config

# The Azure IoT SDK is only required when messages are actually sent to IoT Hub.
# Dry-run mode works locally without it.
try:
    from azure.iot.device import IoTHubDeviceClient, Message
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False


# ─────────────────────────────────────────────────────────────
#  VIN mapping
# ─────────────────────────────────────────────────────────────

def build_vin_map(vehicle_ids: list[str]) -> dict[str, str]:
    """
    Map source vehicle IDs to stable demo VINs.

    VehicleA_001~032        -> VIN-001~032
    VehicleB_001~038        -> VIN-033~070
    VehicleGaussian_001~015 -> VIN-071~085
    VehicleDeg_001~015      -> VIN-086~100
    """
    vin_map: dict[str, str] = {}
    a_ids = sorted(v for v in vehicle_ids if v.startswith("VehicleA_"))
    b_ids = sorted(v for v in vehicle_ids if v.startswith("VehicleB_"))

    for i, vid in enumerate(a_ids, start=1):
        vin_map[vid] = f"VIN-{i:03d}"
    offset = len(a_ids)
    for i, vid in enumerate(b_ids, start=1):
        vin_map[vid] = f"VIN-{offset + i:03d}"

    syn_start = offset + len(b_ids) + 1
    for i in range(1, config.GAUSSIAN_COUNT + 1):
        vin_map[f"VehicleGaussian_{i:03d}"] = f"VIN-{syn_start + i - 1:03d}"

    deg_start = syn_start + config.GAUSSIAN_COUNT
    for i in range(1, config.DEGRADATION_COUNT + 1):
        vin_map[f"VehicleDeg_{i:03d}"] = f"VIN-{deg_start + i - 1:03d}"

    return vin_map


# ─────────────────────────────────────────────────────────────
#  Vehicle metadata (model name / base GPS location)
# ─────────────────────────────────────────────────────────────

def build_vehicle_meta(vin_map: dict[str, str]) -> dict[str, dict]:
    """
    Assign a deterministic model name and base GPS location to every VIN.

    Anomaly vehicles are pinned to Sydney CBD (LOCATION_ANOMALY).
    Normal vehicles are distributed across Australian cities according to
    the risk-weighted distribution defined in CITY_LOCATIONS:
      Sydney (35%) > Melbourne (28%) > Brisbane (18%) >
      Adelaide (9%) > Perth (4%) > other cities (6%).
    """
    rng = random.Random(42)
    meta: dict[str, dict] = {}

    cities  = config.CITY_LOCATIONS
    weights = [c["weight"] for c in cities]

    for orig_id, vin in vin_map.items():
        is_anomaly = vin in config.ANOMALY_VEHICLE_VINS
        if is_anomaly:
            loc = config.LOCATION_ANOMALY
        else:
            loc = rng.choices(cities, weights=weights, k=1)[0]

        meta[vin] = {
            "model_name":         rng.choice(config.BMW_MODELS),
            "base_lat":           rng.uniform(loc["lat_min"], loc["lat_max"]),
            "base_lon":           rng.uniform(loc["lon_min"], loc["lon_max"]),
            "is_anomaly_vehicle": is_anomaly,
        }
    return meta


# ─────────────────────────────────────────────────────────────
#  Gaussian synthetic vehicles (VIN-071~085, 15 vehicles)
# ─────────────────────────────────────────────────────────────

def generate_gaussian_vehicles(
    real_data: dict[str, pd.DataFrame],
    rng: np.random.Generator,
) -> dict[str, pd.DataFrame]:
    """
    Generate synthetic vehicles by adding Gaussian noise to real traces.

    Noise is applied only to the 4 raw sensor columns defined in
    GAUSSIAN_NOISE_COLS. Derived variables (Delta_I, Delta_V, JHS) are
    recalculated at send time from the noisy sensor values.
    """
    all_df = pd.concat(real_data.values(), ignore_index=True)
    col_std = {
        col: float(all_df[col].std())
        for col in config.GAUSSIAN_NOISE_COLS
        if col in all_df.columns
    }

    base_ids = list(real_data.keys())
    syn: dict[str, pd.DataFrame] = {}

    for i in range(1, config.GAUSSIAN_COUNT + 1):
        syn_id  = f"VehicleGaussian_{i:03d}"
        base_id = base_ids[rng.integers(0, len(base_ids))]
        df = real_data[base_id].copy()

        for col, std in col_std.items():
            noise   = rng.normal(0.0, std * config.GAUSSIAN_NOISE_SCALE, size=len(df))
            df[col] = df[col] + noise

        # Clip physically unrealistic sensor values.
        df["voltage"]      = df["voltage"].clip(lower=280.0, upper=420.0)
        df["battery_temp"] = df["battery_temp"].clip(lower=-5.0, upper=50.0)

        df["vehicle_id"] = syn_id
        syn[syn_id] = df.reset_index(drop=True)

    print(f"[AUGMENT] Generated {len(syn)} Gaussian synthetic vehicles")
    return syn


# ─────────────────────────────────────────────────────────────
#  Degradation synthetic vehicles (VIN-086~100, 15 vehicles)
# ─────────────────────────────────────────────────────────────

def generate_degradation_vehicles(
    real_data: dict[str, pd.DataFrame],
    rng: np.random.Generator,
) -> dict[str, pd.DataFrame]:
    """
    Generate degradation vehicles from high-risk real traces.

    A linear progress factor (np.linspace 0 -> 1 over n_rows) is applied to
    raw sensor values only. Joule_Heating_Stress increases naturally as
    temperature rises. Azure ML detects the degradation through Z-score
    and BSI calculation on the received sensor stream.
    """
    base_pool = [v for v in config.DEGRADATION_BASE_VEHICLES if v in real_data]
    if not base_pool:
        base_pool = list(real_data.keys())

    syn: dict[str, pd.DataFrame] = {}

    for i in range(1, config.DEGRADATION_COUNT + 1):
        syn_id  = f"VehicleDeg_{i:03d}"
        base_id = base_pool[rng.integers(0, len(base_pool))]
        df = real_data[base_id].copy()
        n  = len(df)

        progress = np.linspace(0.0, 1.0, n)

        # Voltage drop: simulates capacity fade from battery ageing.
        df["voltage"] = (
            df["voltage"] - config.DEGRADATION_VOLTAGE_DROP_MAX * progress
        ).clip(lower=250.0)

        # Temperature rise: simulates increasing internal resistance.
        df["battery_temp"] = (
            df["battery_temp"] + config.DEGRADATION_TEMP_RISE_MAX * progress
        ).clip(upper=60.0)

        # Joule_Heating_Stress = I^2 * T increases naturally as T rises.
        # Azure ML detects this escalation through Z-score and BSI.

        df["vehicle_id"] = syn_id
        syn[syn_id] = df.reset_index(drop=True)

    print(f"[AUGMENT] Generated {len(syn)} degradation synthetic vehicles")
    return syn


# ─────────────────────────────────────────────────────────────
#  CSV loader
# ─────────────────────────────────────────────────────────────

# Only these 5 columns are required from the preprocessed CSV.
# All other columns (BSI, Z-scores, status labels, etc.) are ignored —
# Azure ML recomputes them from the streamed sensor values.
REQUIRED_COLS = [
    "vehicle_id",
    "voltage",       # Battery voltage [V]
    "current",       # Battery current [A]
    "battery_temp",  # Battery temperature [°C]
    "ambient_temp",  # Ambient temperature [°C]
]

def load_csv(csv_path: str) -> dict[str, pd.DataFrame]:
    """Read the preprocessed CSV and return one DataFrame per source vehicle_id."""
    print(f"[LOAD] {csv_path}")
    df = pd.read_csv(csv_path, low_memory=False)

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in CSV: {missing}")

    split: dict[str, pd.DataFrame] = {}
    for vid, grp in df.groupby("vehicle_id"):
        split[str(vid)] = grp.reset_index(drop=True)

    print(f"[LOAD] Loaded {len(split)} vehicles and {len(df):,} rows")
    return split


# ─────────────────────────────────────────────────────────────
#  Location helpers
# ─────────────────────────────────────────────────────────────

def _jitter_location(base_lat: float, base_lon: float) -> tuple[float, float]:
    """Apply a small random jitter (~100 m) to simulate vehicle movement."""
    return (
        round(base_lat + random.uniform(-0.001, 0.001), 6),
        round(base_lon + random.uniform(-0.001, 0.001), 6),
    )


# Australian suburb bounding boxes: (lat_min, lat_max, lon_min, lon_max, region_id, region_name)
# Coordinates use GDA2020 (WGS84-compatible). Southern hemisphere latitudes are negative.
# City catch-all boxes sit at the end of each city block — first match wins.
# Anomaly vehicles (Sydney CBD coords) always resolve to region_id=101.
_AUSTRALIA_SUBURBS: list[tuple[float, float, float, float, int, str]] = [

    # ── Sydney, NSW (region_id 101–115, catch-all 901) ──────────
    (-33.880, -33.855, 151.188, 151.218, 101, "Sydney CBD, NSW"),
    (-33.902, -33.878, 151.198, 151.228, 102, "Surry Hills, NSW"),
    (-33.912, -33.888, 151.168, 151.198, 103, "Newtown, NSW"),
    (-33.902, -33.878, 151.258, 151.292, 104, "Bondi, NSW"),
    (-33.928, -33.905, 151.228, 151.260, 105, "Randwick, NSW"),
    (-33.828, -33.805, 150.992, 151.022, 106, "Parramatta, NSW"),
    (-33.812, -33.788, 151.170, 151.202, 107, "Chatswood, NSW"),
    (-33.812, -33.786, 151.270, 151.300, 108, "Manly, NSW"),
    (-33.720, -33.692, 151.080, 151.115, 109, "Hornsby, NSW"),
    (-33.935, -33.910, 150.912, 150.945, 110, "Liverpool, NSW"),
    (-33.765, -33.738, 150.680, 150.715, 111, "Penrith, NSW"),
    (-34.048, -34.022, 151.048, 151.078, 112, "Sutherland, NSW"),
    (-33.852, -33.825, 151.095, 151.130, 113, "Ryde, NSW"),
    (-33.998, -33.970, 151.095, 151.128, 114, "Hurstville, NSW"),
    (-33.768, -33.742, 150.935, 150.968, 115, "Blacktown, NSW"),
    (-34.10,  -33.70,  150.85, 151.40,  901, "Sydney, NSW"),        # city catch-all

    # ── Melbourne, VIC (region_id 121–132, catch-all 902) ───────
    (-37.828, -37.802, 144.945, 144.980, 121, "Melbourne CBD, VIC"),
    (-37.840, -37.815, 144.985, 145.015, 122, "Richmond, VIC"),
    (-37.818, -37.793, 144.968, 144.998, 123, "Fitzroy, VIC"),
    (-37.885, -37.858, 144.968, 144.998, 124, "St Kilda, VIC"),
    (-37.792, -37.765, 144.950, 144.982, 125, "Brunswick, VIC"),
    (-37.815, -37.788, 144.888, 144.920, 126, "Footscray, VIC"),
    (-37.805, -37.778, 145.112, 145.145, 127, "Doncaster, VIC"),
    (-37.835, -37.808, 145.112, 145.145, 128, "Box Hill, VIC"),
    (-37.838, -37.812, 145.015, 145.042, 129, "Hawthorn, VIC"),
    (-37.952, -37.925, 145.048, 145.078, 130, "Moorabbin, VIC"),
    (-37.822, -37.795, 144.852, 144.885, 131, "Sunshine, VIC"),
    (-37.862, -37.835, 145.090, 145.122, 132, "Glen Waverley, VIC"),
    (-38.00,  -37.65,  144.80, 145.20,  902, "Melbourne, VIC"),     # city catch-all

    # ── Brisbane, QLD (region_id 141–150, catch-all 903) ────────
    (-27.485, -27.458, 153.010, 153.045, 141, "Brisbane CBD, QLD"),
    (-27.468, -27.445, 153.028, 153.060, 142, "Fortitude Valley, QLD"),
    (-27.495, -27.470, 153.010, 153.042, 143, "South Brisbane, QLD"),
    (-27.508, -27.482, 153.028, 153.060, 144, "Woolloongabba, QLD"),
    (-27.500, -27.475, 152.978, 153.005, 145, "Toowong, QLD"),
    (-27.412, -27.382, 153.018, 153.050, 146, "Chermside, QLD"),
    (-27.555, -27.528, 153.055, 153.088, 147, "Mount Gravatt, QLD"),
    (-27.518, -27.490, 153.092, 153.125, 148, "Carindale, QLD"),
    (-27.528, -27.502, 152.965, 152.998, 149, "Indooroopilly, QLD"),
    (-27.450, -27.422, 153.058, 153.090, 150, "Clayfield, QLD"),
    (-27.65,  -27.35,  152.90, 153.25,  903, "Brisbane, QLD"),      # city catch-all

    # ── Adelaide, SA (region_id 161–168, catch-all 904) ─────────
    (-34.942, -34.918, 138.588, 138.620, 161, "Adelaide CBD, SA"),
    (-34.935, -34.910, 138.618, 138.652, 162, "Norwood, SA"),
    (-34.995, -34.968, 138.505, 138.535, 163, "Glenelg, SA"),
    (-34.910, -34.882, 138.588, 138.620, 164, "Prospect, SA"),
    (-34.975, -34.950, 138.582, 138.615, 165, "Unley, SA"),
    (-34.952, -34.925, 138.638, 138.672, 166, "Burnside, SA"),
    (-34.900, -34.872, 138.635, 138.668, 167, "Campbelltown, SA"),
    (-34.972, -34.948, 138.542, 138.572, 168, "Glenelg North, SA"),
    (-35.00,  -34.80,  138.50, 138.75,  904, "Adelaide, SA"),       # city catch-all

    # ── Perth, WA (region_id 171–178, catch-all 905) ─────────────
    (-31.968, -31.942, 115.848, 115.882, 171, "Perth CBD, WA"),
    (-32.072, -32.045, 115.738, 115.770, 172, "Fremantle, WA"),
    (-31.965, -31.938, 115.818, 115.852, 173, "Subiaco, WA"),
    (-32.008, -31.982, 115.738, 115.770, 174, "Cottesloe, WA"),
    (-31.762, -31.732, 115.748, 115.778, 175, "Joondalup, WA"),
    (-31.908, -31.878, 115.748, 115.778, 176, "Scarborough, WA"),
    (-31.998, -31.972, 115.808, 115.840, 177, "South Perth, WA"),
    (-31.938, -31.908, 115.780, 115.812, 178, "Claremont, WA"),
    (-32.10,  -31.85,  115.75, 115.98,  905, "Perth, WA"),          # city catch-all

    # ── Other Australian cities (region_id 181–190) ──────────────
    (-28.032, -28.005, 153.395, 153.432, 181, "Gold Coast, QLD"),
    (-28.048, -28.022, 153.422, 153.455, 182, "Broadbeach, QLD"),
    (-32.942, -32.918, 151.762, 151.798, 183, "Newcastle, NSW"),
    (-35.325, -35.298, 149.112, 149.148, 184, "Canberra, ACT"),
    (-42.902, -42.872, 147.312, 147.348, 185, "Hobart, TAS"),
    (-12.482, -12.450, 130.830, 130.865, 186, "Darwin, NT"),
    (-16.942, -16.908, 145.758, 145.795, 187, "Cairns, QLD"),
    (-34.442, -34.412, 150.878, 150.915, 188, "Wollongong, NSW"),
    (-38.168, -38.138, 144.340, 144.382, 189, "Geelong, VIC"),
    (-19.278, -19.248, 146.808, 146.845, 190, "Townsville, QLD"),
]


def _region_from_latlon(lat: float, lon: float) -> tuple[int, str]:
    """Return (region_id, suburb_name) for the given GPS coordinates."""
    for lat_min, lat_max, lon_min, lon_max, rid, rname in _AUSTRALIA_SUBURBS:
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            return rid, rname
    return 1, "Australia"   # Fallback: coordinate outside all defined bounding boxes


# ─────────────────────────────────────────────────────────────
#  JSON payload builder
# ─────────────────────────────────────────────────────────────

def build_payload(
    row:            pd.Series,
    vin:            str,
    meta:           dict,
    prev:           dict | None,   # Previous tick: {"voltage": float, "current": float}
    inject_anomaly: bool = False,
) -> dict:
    """
    Read raw sensor values, compute derived variables, and return a JSON payload
    aligned with the Azure SQL / Stream Analytics schema.

    Derived variables (per BSI methodology document):
      Delta_I              = I(t) - I(t-1)   [A]
      Delta_V              = V(t) - V(t-1)   [V]
      Joule_Heating_Stress = I(t)^2 * T(t)   [A^2·°C]

    BSI weights: Delta_I (0.4830) > Delta_V (0.2218) > JHS (0.1027) > Current (0.0992) > Voltage (0.0933)
    Weights derived from NASA PCoE battery dataset analysis (PCA on Z-score abnormality matrix).

    Z-score, BSI, and status are NOT computed here — Azure ML handles all inference.
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Read raw sensor values from the CSV row.
    voltage      = float(row["voltage"])
    current      = float(row["current"])
    battery_temp = float(row["battery_temp"])
    ambient_temp = float(row["ambient_temp"])

    # Optional anomaly injection for pipeline stress testing.
    # Remove for production — Azure ML detects anomalies independently.
    if inject_anomaly:
        voltage      -= random.uniform(*config.ANOMALY_VOLTAGE_DROP)
        battery_temp += random.uniform(*config.ANOMALY_TEMP_RISE)
        current      -= random.uniform(*config.ANOMALY_CURRENT_EXTRA)

    # Compute derived variables in real time.
    # First tick per vehicle has no previous values: deltas are set to 0.0.
    if prev is not None:
        delta_i = round(current - prev["current"], 6)   # Delta_I [A]
        delta_v = round(voltage - prev["voltage"], 6)   # Delta_V [V]
    else:
        delta_i = 0.0
        delta_v = 0.0

    # Joule Heating Stress: derived from Joule's law Q = I^2 * R * t
    joule_heating_stress = round((current ** 2) * battery_temp, 4)

    # Apply small GPS jitter to simulate vehicle movement (~100 m radius).
    lat, lon = _jitter_location(meta["base_lat"], meta["base_lon"])
    region_id, region_name = _region_from_latlon(lat, lon)

    return {
        # ── Vehicle identification ──────────────────────────
        "vehicle_id":  vin,
        "model_name":  meta["model_name"],
        "received_at": now_iso,

        # ── Raw sensor values (Battery_Telemetry table) ─────
        "battery_voltage": round(voltage,      4),
        "battery_current": round(current,      4),
        "temperature":     round(battery_temp, 4),
        "ambient_temp":    round(ambient_temp, 4),

        # ── Derived variables (Stream Analytics -> Azure ML) ─
        "delta_i":              delta_i,
        "delta_v":              delta_v,
        "joule_heating_stress": joule_heating_stress,

        # ── Location ────────────────────────────────────────
        "latitude":          lat,
        "longitude":         lon,
        "current_region_id": region_id,
        "region_name":       region_name,

        # ── Operational status ──────────────────────────────
        "is_active":        1,
        "last_received_at": now_iso,
    }


# ─────────────────────────────────────────────────────────────
#  Main playback loop
# ─────────────────────────────────────────────────────────────

def run(csv_path: str, dry_run: bool, interval: float, rows_per_send: int) -> None:
    """Run the real-time playback loop until interrupted by Ctrl+C."""
    vehicle_data = load_csv(csv_path)

    # Generate synthetic vehicles with a fixed seed for reproducibility.
    rng = np.random.default_rng(seed=42)
    vehicle_data.update(generate_gaussian_vehicles(vehicle_data, rng))
    vehicle_data.update(generate_degradation_vehicles(vehicle_data, rng))

    vin_map  = build_vin_map(list(vehicle_data.keys()))
    vin_meta = build_vehicle_meta(vin_map)

    # Per-vehicle state.
    cursors:     dict[str, int]  = {vid: 0 for vid in vehicle_data}
    prev_values: dict[str, dict] = {}   # Stores previous tick's voltage + current per vehicle.

    client = None
    if not dry_run:
        if not _SDK_AVAILABLE:
            raise RuntimeError(
                "azure-iot-device not installed. Run: pip install -r requirements.txt"
            )
        client = IoTHubDeviceClient.create_from_connection_string(
            config.IOT_HUB_CONNECTION_STRING
        )
        client.connect()
        print("[IOT] Connected to IoT Hub")

    print(f"[START] {len(vehicle_data)} vehicles / interval={interval}s / rows_per_send={rows_per_send}")
    print("[START] Press Ctrl+C to stop\n")

    sent_total = 0
    try:
        while True:
            tick_start = time.time()

            for vid, df in vehicle_data.items():
                vin  = vin_map.get(vid, vid)
                meta = vin_meta.get(vin, {
                    "model_name": "BMW i4 eDrive40",
                    "base_lat": -33.868, "base_lon": 151.209,   # Sydney CBD default
                    "is_anomaly_vehicle": False,
                })

                idx = cursors[vid]
                row = df.iloc[idx]

                # 1% chance of random anomaly injection for pipeline stress testing.
                inject = random.random() < config.ANOMALY_PROB

                payload = build_payload(
                    row            = row,
                    vin            = vin,
                    meta           = meta,
                    prev           = prev_values.get(vid),
                    inject_anomaly = inject,
                )

                # Store this tick's sensor readings for delta calculation on the next tick.
                prev_values[vid] = {
                    "voltage": float(row["voltage"]),
                    "current": float(row["current"]),
                }

                if dry_run:
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    msg = Message(json.dumps(payload))
                    msg.content_type     = "application/json"
                    msg.content_encoding = "utf-8"
                    client.send_message(msg)

                sent_total += 1
                cursors[vid] = (idx + rows_per_send) % len(df)

            elapsed   = time.time() - tick_start
            sleep_sec = max(0.0, interval - elapsed)
            if sleep_sec > 0:
                time.sleep(sleep_sec)

            if sent_total % (len(vehicle_data) * 10) == 0:
                print(f"[INFO] Messages sent: {sent_total:,}")

    except KeyboardInterrupt:
        print(f"\n[STOP] Stopped — total messages sent: {sent_total:,}")
    finally:
        if client:
            client.disconnect()


# ─────────────────────────────────────────────────────────────
#  CLI entry point
# ─────────────────────────────────────────────────────────────

def main() -> None:
    """Parse CLI arguments and start the simulator."""
    parser = argparse.ArgumentParser(description="EV-Pulse BMW Playback Simulator v2")
    parser.add_argument(
        "--csv",
        default="TripAB_all_processed_sampled_60000_window_combined.csv",
        help="Path to the preprocessed CSV (default: same folder as this script).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON to the console without sending to IoT Hub.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=config.SEND_INTERVAL_SEC,
        help="Send interval in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--rows-per-send",
        type=int,
        default=config.ROWS_PER_SEND,
        help="CSV rows to advance per vehicle per tick (default: 10).",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        alt = Path(__file__).parent / args.csv
        if alt.exists():
            csv_path = alt
        else:
            raise FileNotFoundError(f"CSV file not found: {args.csv}")

    run(
        csv_path      = str(csv_path),
        dry_run       = args.dry_run,
        interval      = args.interval,
        rows_per_send = args.rows_per_send,
    )


if __name__ == "__main__":
    main()
