"""
EV-Pulse Simulator v2 — config.py

Role separation:
  Simulator  : generates raw sensor values + derived variables (Delta_I, Delta_V, JHS)
  Azure ML   : computes Z-scores, BSI, and classifies Normal / Warning / Critical

Keep this file safe. Never commit real IoT Hub connection strings to version control.
"""

# ── Azure IoT Hub device connection string ─────────────────────
# Replace this placeholder locally before running live sends.
IOT_HUB_CONNECTION_STRING = "HostName=<YOUR_IOT_HUB>.azure-devices.net;DeviceId=<DEVICE_ID>;SharedAccessKey=<KEY>"

# ── Playback settings ──────────────────────────────────────────
SEND_INTERVAL_SEC = 1.0    # Send interval in seconds.
ROWS_PER_SEND     = 10     # CSV rows to advance per vehicle per tick.
                           # Source sampling is 0.1 s -> default 10 rows = 1 s playback.
ANOMALY_PROB      = 0.01   # Per-tick probability of injecting random anomaly values (1%).

# ── Vehicle counts ─────────────────────────────────────────────
NUM_REAL_VEHICLES = 70     # Real source vehicles: 32 summer (TripA) + 38 winter (TripB).
NUM_SYN_VEHICLES  = 30     # Synthetic vehicles: 15 Gaussian + 15 degradation.
NUM_VEHICLES      = NUM_REAL_VEHICLES + NUM_SYN_VEHICLES  # 100 total

# High-risk VINs placed in Sydney CBD for Power BI map visualisation.
# Derived from the top-5 danger-ratio vehicles in the preprocessed dataset.
ANOMALY_VEHICLE_VINS = ["VIN-027", "VIN-022", "VIN-064", "VIN-024", "VIN-060"]

# ── High-risk vehicle location (Sydney CBD) ────────────────────
# Anomaly vehicles are pinned to the Sydney CBD bounding box.
LOCATION_ANOMALY = {
    "lat_min": -33.880, "lat_max": -33.855,
    "lon_min":  151.195, "lon_max":  151.225,
}

# ── City distribution for normal vehicles ─────────────────────
# Each entry defines a city bounding box and a selection weight.
# Risk concentration order: Sydney > Melbourne > Brisbane > Adelaide > Perth > other cities.
# Weights must sum to 1.0.
CITY_LOCATIONS = [
    {"lat_min": -34.00, "lat_max": -33.70, "lon_min": 150.85, "lon_max": 151.40, "weight": 0.35},  # Sydney
    {"lat_min": -38.00, "lat_max": -37.65, "lon_min": 144.80, "lon_max": 145.20, "weight": 0.28},  # Melbourne
    {"lat_min": -27.65, "lat_max": -27.35, "lon_min": 152.90, "lon_max": 153.25, "weight": 0.18},  # Brisbane
    {"lat_min": -35.00, "lat_max": -34.80, "lon_min": 138.50, "lon_max": 138.75, "weight": 0.09},  # Adelaide
    {"lat_min": -32.10, "lat_max": -31.85, "lon_min": 115.75, "lon_max": 115.98, "weight": 0.04},  # Perth
    {"lat_min": -28.10, "lat_max": -27.95, "lon_min": 153.35, "lon_max": 153.55, "weight": 0.015}, # Gold Coast
    {"lat_min": -32.95, "lat_max": -32.85, "lon_min": 151.70, "lon_max": 151.82, "weight": 0.015}, # Newcastle
    {"lat_min": -35.40, "lat_max": -35.25, "lon_min": 149.05, "lon_max": 149.22, "weight": 0.010}, # Canberra
    {"lat_min": -42.92, "lat_max": -42.82, "lon_min": 147.28, "lon_max": 147.40, "weight": 0.005}, # Hobart
    {"lat_min": -12.48, "lat_max": -12.40, "lon_min": 130.83, "lon_max": 130.95, "weight": 0.005}, # Darwin
]

# ── BMW model names ────────────────────────────────────────────
BMW_MODELS = [
    "BMW i4 eDrive40",
    "BMW iX1 xDrive30",
    "BMW i7 xDrive60",
    "BMW i5 eDrive40",
    "BMW iX xDrive50",
]

# ── Random anomaly injection ranges (pipeline stress-test only) ─
# BSI / status are NOT injected here — Azure ML handles classification.
ANOMALY_VOLTAGE_DROP  = (10.0, 30.0)   # Voltage drop in volts.
ANOMALY_TEMP_RISE     = (5.0,  12.0)   # Battery temperature rise in °C.
ANOMALY_CURRENT_EXTRA = (3.0,  8.0)    # Additional discharge-current stress in amps.

# ── Gaussian synthetic vehicles (VIN-071~085, 15 vehicles) ─────
GAUSSIAN_COUNT       = 15
GAUSSIAN_NOISE_SCALE = 0.08   # Noise magnitude: column std × 8%.
# Only raw sensor columns receive noise; derived variables are recalculated at send time.
GAUSSIAN_NOISE_COLS  = ["voltage", "current", "battery_temp", "ambient_temp"]

# ── Degradation synthetic vehicles (VIN-086~100, 15 vehicles) ──
DEGRADATION_COUNT   = 15
# Source pool: highest-risk vehicles by danger ratio.
DEGRADATION_BASE_VEHICLES = [
    "VehicleA_027", "VehicleA_022", "VehicleB_032",
    "VehicleA_024", "VehicleB_028", "VehicleB_022",
    "VehicleA_014", "VehicleA_002", "VehicleA_001",
    "VehicleA_025",
]
# Maximum degradation at progress = 1.0 (end of CSV playback).
# Applied linearly via np.linspace(0, 1, n_rows) — only raw sensor values are modified.
DEGRADATION_VOLTAGE_DROP_MAX = 25.0   # Maximum voltage drop in volts.
DEGRADATION_TEMP_RISE_MAX    = 10.0   # Maximum temperature rise in °C.
