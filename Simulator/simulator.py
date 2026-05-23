#!/usr/bin/env python3
"""
EV-Pulse BMW Playback Simulator

Replays a preprocessed BMW i3 telemetry CSV as a real-time Azure IoT stream.
The simulator assigns stable VINs, generates synthetic vehicles, injects rare
anomalies, confirms CRITICAL states only after consecutive violations, and
emits JSON payloads aligned with the Azure SQL / Power BI dashboard schema.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import config

# The Azure IoT SDK is only required when messages are actually sent to IoT Hub.
# Dry-run mode can still run locally without the SDK being installed.
try:
    from azure.iot.device import IoTHubDeviceClient, Message
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False


REQUIRED_COLS = [
    "vehicle_id",
    "voltage", "current", "battery_temp", "ambient_temp",
    "delta_v", "delta_i",
    "joule_heating_stress", "thermal_temperature_70min",
    "thermal_stress",
    "Z_Delta_I", "Z_Delta_V",
    "Z_Battery_Current", "Z_Battery_Voltage",
    "Z_Thermal_Stress", "Z_Joule_Heating_Stress",
    "BSI", "Z_BSI",
    "current_status_label",
]


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

    for i, vehicle_id in enumerate(a_ids, start=1):
        vin_map[vehicle_id] = f"VIN-{i:03d}"

    offset = len(a_ids)
    for i, vehicle_id in enumerate(b_ids, start=1):
        vin_map[vehicle_id] = f"VIN-{offset + i:03d}"

    synthetic_start = offset + len(b_ids) + 1
    for i in range(1, config.GAUSSIAN_COUNT + 1):
        vin_map[f"VehicleGaussian_{i:03d}"] = f"VIN-{synthetic_start + i - 1:03d}"

    degradation_start = synthetic_start + config.GAUSSIAN_COUNT
    for i in range(1, config.DEGRADATION_COUNT + 1):
        vin_map[f"VehicleDeg_{i:03d}"] = f"VIN-{degradation_start + i - 1:03d}"

    return vin_map


def build_vehicle_meta(vin_map: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Assign a deterministic model name and base location to every VIN."""
    rng = random.Random(42)
    meta: dict[str, dict[str, Any]] = {}

    for _, vin in vin_map.items():
        is_anomaly_vehicle = vin in config.ANOMALY_VEHICLE_VINS
        location_range = config.LOCATION_ANOMALY if is_anomaly_vehicle else config.LOCATION_NORMAL
        meta[vin] = {
            "model_name": rng.choice(config.BMW_MODELS),
            "base_lat": rng.uniform(location_range["lat_min"], location_range["lat_max"]),
            "base_lon": rng.uniform(location_range["lon_min"], location_range["lon_max"]),
            "is_anomaly_vehicle": is_anomaly_vehicle,
        }

    return meta


def load_csv(csv_path: str) -> dict[str, pd.DataFrame]:
    """Read the input CSV and return one DataFrame per source vehicle_id."""
    print(f"[LOAD] {csv_path}")
    df = pd.read_csv(csv_path, low_memory=False)

    missing_cols = [col for col in REQUIRED_COLS if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required columns in CSV: {missing_cols}")

    vehicle_data: dict[str, pd.DataFrame] = {}
    for vehicle_id, group in df.groupby("vehicle_id"):
        vehicle_data[str(vehicle_id)] = group.reset_index(drop=True)

    print(f"[LOAD] Loaded {len(vehicle_data)} vehicles and {len(df):,} rows")
    return vehicle_data


def generate_gaussian_vehicles(
    real_data: dict[str, pd.DataFrame],
    rng: np.random.Generator,
) -> dict[str, pd.DataFrame]:
    """
    Generate synthetic vehicles by adding light Gaussian noise to real traces.

    The noise level is calculated as each column's standard deviation multiplied
    by GAUSSIAN_NOISE_SCALE. This keeps synthetic vehicles realistic while still
    making them distinct from their source traces.
    """
    all_df = pd.concat(real_data.values(), ignore_index=True)
    col_std: dict[str, float] = {
        col: float(all_df[col].std())
        for col in config.GAUSSIAN_NOISE_COLS
        if col in all_df.columns
    }

    base_ids = list(real_data.keys())
    synthetic_data: dict[str, pd.DataFrame] = {}

    for i in range(1, config.GAUSSIAN_COUNT + 1):
        synthetic_id = f"VehicleGaussian_{i:03d}"
        base_id = base_ids[rng.integers(0, len(base_ids))]
        df = real_data[base_id].copy()

        for col, std in col_std.items():
            noise = rng.normal(0.0, std * config.GAUSSIAN_NOISE_SCALE, size=len(df))
            df[col] = df[col] + noise

        # Clip physically unrealistic values.
        if "voltage" in df.columns:
            df["voltage"] = df["voltage"].clip(lower=280.0, upper=420.0)
        if "battery_temp" in df.columns:
            df["battery_temp"] = df["battery_temp"].clip(lower=-5.0, upper=50.0)
        if "BSI" in df.columns:
            df["BSI"] = df["BSI"].clip(lower=0.0)

        df["vehicle_id"] = synthetic_id
        synthetic_data[synthetic_id] = df.reset_index(drop=True)

    print(f"[AUGMENT] Generated {len(synthetic_data)} Gaussian-noise synthetic vehicles")
    return synthetic_data


def generate_degradation_vehicles(
    real_data: dict[str, pd.DataFrame],
    rng: np.random.Generator,
) -> dict[str, pd.DataFrame]:
    """
    Generate degradation vehicles from high-risk real traces.

    As progress moves from 0 to 1, the simulator applies voltage drop,
    temperature rise, BSI amplification, and thermal-stress increase.
    The status label is recalculated from the degraded BSI.
    """
    base_pool = [vehicle_id for vehicle_id in config.DEGRADATION_BASE_VEHICLES if vehicle_id in real_data]
    if not base_pool:
        base_pool = list(real_data.keys())

    synthetic_data: dict[str, pd.DataFrame] = {}

    for i in range(1, config.DEGRADATION_COUNT + 1):
        synthetic_id = f"VehicleDeg_{i:03d}"
        base_id = base_pool[rng.integers(0, len(base_pool))]
        df = real_data[base_id].copy()
        n_rows = len(df)

        progress = np.linspace(0.0, 1.0, n_rows)

        voltage_drop = config.DEGRADATION_VOLTAGE_DROP_MAX * progress
        df["voltage"] = (df["voltage"] - voltage_drop).clip(lower=250.0)

        temp_rise = config.DEGRADATION_TEMP_RISE_MAX * progress
        df["battery_temp"] = (df["battery_temp"] + temp_rise).clip(upper=60.0)

        bsi_multiplier = 1.0 + (config.DEGRADATION_BSI_AMPLIFY_MAX - 1.0) * progress
        bsi_addition = config.DEGRADATION_BSI_ADD_MAX * progress
        df["BSI"] = (df["BSI"] * bsi_multiplier + bsi_addition).clip(lower=0.0)
        df["Z_BSI"] = df["Z_BSI"] * bsi_multiplier + bsi_addition

        stress_rise = config.DEGRADATION_STRESS_RISE_MAX * progress
        df["thermal_stress"] = df["thermal_stress"] + stress_rise
        df["Z_Thermal_Stress"] = df["Z_Thermal_Stress"] + stress_rise

        def relabel(bsi_value: float) -> float:
            if bsi_value > config.DEGRADATION_CRITICAL_BSI:
                return 2.0
            if bsi_value > config.DEGRADATION_WARNING_BSI:
                return 1.0
            return 0.0

        df["current_status_label"] = df["BSI"].apply(relabel)
        df["vehicle_id"] = synthetic_id
        synthetic_data[synthetic_id] = df.reset_index(drop=True)

    print(f"[AUGMENT] Generated {len(synthetic_data)} degradation synthetic vehicles")
    return synthetic_data


def jitter_location(base_lat: float, base_lon: float) -> tuple[float, float]:
    """Apply a small location jitter to mimic vehicle movement."""
    return (
        round(base_lat + random.uniform(-0.001, 0.001), 6),
        round(base_lon + random.uniform(-0.001, 0.001), 6),
    )


def region_from_latlon(lat: float, lon: float) -> int:
    """Map coordinates to demo Region.region_id values used by Azure SQL."""
    if 37.490 <= lat <= 37.530 and 127.020 <= lon <= 127.090:
        return 101  # Gangnam-gu
    if 37.540 <= lat <= 37.580 and 126.880 <= lon <= 126.930:
        return 102  # Mapo-gu
    return 1        # Seoul default


def build_payload(
    row: pd.Series,
    vin: str,
    meta: dict[str, Any],
    anomaly_prob: float,
) -> dict[str, Any]:
    """
    Convert one telemetry row into a JSON payload aligned with the SQL schema.

    The final CRITICAL confirmation is handled later in run() through the
    consecutive-violation streak logic.
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    voltage = float(row["voltage"])
    current_a = float(row["current"])
    temperature = float(row["battery_temp"])
    ambient = float(row["ambient_temp"])
    bsi = float(row["BSI"])
    z_bsi = float(row["Z_BSI"])
    label = float(row["current_status_label"])
    status = config.STATUS_MAP.get(label, "NORMAL")

    delta_v = float(row["delta_v"])
    delta_i = float(row["delta_i"])
    thermal_stress = float(row["thermal_stress"])
    joule_stress = float(row["joule_heating_stress"])
    thermal_70min = float(row["thermal_temperature_70min"])
    z_delta_i = float(row["Z_Delta_I"])
    z_delta_v = float(row["Z_Delta_V"])
    z_thermal_stress = float(row["Z_Thermal_Stress"])

    alert_type = "BATTERY_STRESS"
    is_anomaly = 0

    # Rare random anomaly injection for live dashboard demonstration.
    if random.random() < anomaly_prob:
        voltage -= random.uniform(*config.ANOMALY_VOLTAGE_DROP)
        temperature += random.uniform(*config.ANOMALY_TEMP_RISE)
        current_a -= random.uniform(*config.ANOMALY_CURRENT_EXTRA)
        status = "CRITICAL"
        alert_type = "RANDOM_INJECTION"

    # Label 2 means Danger in the source BSI pseudo-label.
    if label == 2.0:
        is_anomaly = 1

    lat, lon = jitter_location(meta["base_lat"], meta["base_lon"])
    region_id = region_from_latlon(lat, lon)

    return {
        # Vehicle / VehicleModel fields.
        "vehicle_id": vin,
        "model_name": meta["model_name"],

        # Shared event timestamp.
        "received_at": now_iso,

        # Battery_Telemetry fields.
        "battery_voltage": round(voltage, 4),
        "battery_current": round(current_a, 4),
        "temperature": round(temperature, 4),
        "bsi": round(bsi, 6),
        "status": status,
        "latitude": lat,
        "longitude": lon,

        # BSI_Feature_Log fields.
        "delta_v": round(delta_v, 6),
        "delta_i": round(delta_i, 6),
        "thermal_stress": round(thermal_stress, 6),
        "z_delta_i": round(z_delta_i, 6),
        "z_delta_v": round(z_delta_v, 6),
        "z_thermal_stress": round(z_thermal_stress, 6),

        # Vehicle_Current_Status fields.
        "current_bsi": round(bsi, 6),
        "last_received_at": now_iso,
        "is_active": 1,
        "current_region_id": region_id,

        # Alert_Log fields. Stream Analytics can insert alerts when is_anomaly == 1.
        "is_anomaly": is_anomaly,
        "alert_time": now_iso,
        "alert_type": alert_type,
        "alert_level": status,
        "message": f"{vin} battery status: {status}",
        "is_sent_teams": 0,

        # Extra debugging fields not required by the SQL dashboard schema.
        "ambient_temp": round(ambient, 4),
        "joule_heating_stress": round(joule_stress, 4),
        "thermal_temp_70min": round(thermal_70min, 4),
        "z_bsi": round(z_bsi, 6),
    }


def run(csv_path: str, dry_run: bool, interval: float, rows_per_send: int) -> None:
    """Run the real-time playback loop until interrupted by Ctrl+C."""
    vehicle_data = load_csv(csv_path)

    rng = np.random.default_rng(seed=42)
    vehicle_data.update(generate_gaussian_vehicles(vehicle_data, rng))
    vehicle_data.update(generate_degradation_vehicles(vehicle_data, rng))

    vin_map = build_vin_map(list(vehicle_data.keys()))
    vin_meta = build_vehicle_meta(vin_map)

    cursors: dict[str, int] = {vehicle_id: 0 for vehicle_id in vehicle_data}
    streaks: dict[str, int] = {vehicle_id: 0 for vehicle_id in vehicle_data}

    client = None
    if not dry_run:
        if not SDK_AVAILABLE:
            raise RuntimeError("azure-iot-device is not installed. Run: pip install -r requirements.txt")
        client = IoTHubDeviceClient.create_from_connection_string(config.IOT_HUB_CONNECTION_STRING)
        client.connect()
        print("[IOT] Connected to IoT Hub")

    print(f"[START] {len(vehicle_data)} vehicles / interval={interval}s / rows_per_send={rows_per_send}")
    print("[START] Press Ctrl+C to stop\n")

    sent_total = 0
    try:
        while True:
            tick_start = time.time()

            for vehicle_id, df in vehicle_data.items():
                vin = vin_map.get(vehicle_id, vehicle_id)
                meta = vin_meta.get(vin, {
                    "model_name": "BMW i3 (120Ah)",
                    "base_lat": 37.55,
                    "base_lon": 127.00,
                    "is_anomaly_vehicle": False,
                })

                idx = cursors[vehicle_id]
                row = df.iloc[idx]

                payload = build_payload(row, vin, meta, anomaly_prob=config.ANOMALY_PROB)

                tentative_status = payload["status"]
                if tentative_status != "NORMAL":
                    streaks[vehicle_id] += 1
                else:
                    streaks[vehicle_id] = 0

                streak = streaks[vehicle_id]
                if streak >= config.CRITICAL_STREAK_THRESHOLD:
                    payload["status"] = "CRITICAL"
                    payload["is_anomaly"] = 1
                    payload["alert_level"] = "CRITICAL"
                    payload["message"] = f"{vin} CRITICAL confirmed ({streak} consecutive violations)"
                elif tentative_status != "NORMAL" and streak < config.CRITICAL_STREAK_THRESHOLD:
                    payload["status"] = "WARNING"
                    payload["is_anomaly"] = 0
                    payload["alert_level"] = "WARNING"
                    payload["message"] = f"{vin} WARNING (streak {streak}/{config.CRITICAL_STREAK_THRESHOLD})"

                if dry_run:
                    print(json.dumps(payload, ensure_ascii=False))
                else:
                    message = Message(json.dumps(payload))
                    message.content_type = "application/json"
                    message.content_encoding = "utf-8"
                    client.send_message(message)

                sent_total += 1
                cursors[vehicle_id] = (idx + rows_per_send) % len(df)

            elapsed = time.time() - tick_start
            sleep_sec = max(0.0, interval - elapsed)
            if sleep_sec > 0:
                time.sleep(sleep_sec)

            if sent_total % (len(vehicle_data) * 10) == 0:
                print(f"[INFO] Sent {sent_total:,} messages")

    except KeyboardInterrupt:
        print(f"\n[STOP] Stopped — total messages sent: {sent_total:,}")
    finally:
        if client:
            client.disconnect()


def main() -> None:
    """Parse CLI arguments and start the simulator."""
    parser = argparse.ArgumentParser(description="EV-Pulse BMW Playback Simulator")
    parser.add_argument(
        "--csv",
        default="TripAB_all_processed_sampled_60000_window_combined.csv",
        help="Path to the preprocessed CSV file. Default: same folder as this script.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print JSON to the console without sending messages to IoT Hub.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=config.SEND_INTERVAL_SEC,
        help="Send interval in seconds. Default: 1.0.",
    )
    parser.add_argument(
        "--rows-per-send",
        type=int,
        default=config.ROWS_PER_SEND,
        help="Number of CSV rows to skip per vehicle on each tick. Default: 10.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        alt_path = Path(__file__).parent / args.csv
        if alt_path.exists():
            csv_path = alt_path
        else:
            raise FileNotFoundError(f"CSV file not found: {args.csv}")

    run(
        csv_path=str(csv_path),
        dry_run=args.dry_run,
        interval=args.interval,
        rows_per_send=args.rows_per_send,
    )


if __name__ == "__main__":
    main()
