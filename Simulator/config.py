"""
EV-Pulse simulator configuration.

Keep this file safe for public repositories. Do not commit real IoT Hub device
connection strings, subscription IDs, account keys, or any other secret values.
"""

# Azure IoT Hub device connection string.
# Replace this placeholder locally before running live sends.
IOT_HUB_CONNECTION_STRING = "HostName=<YOUR_IOT_HUB>.azure-devices.net;DeviceId=<DEVICE_ID>;SharedAccessKey=<KEY>"

# Playback settings.
SEND_INTERVAL_SEC = 1.0      # Send interval in seconds.
ROWS_PER_SEND = 10           # 0.1s source sampling -> 1s compressed playback.
ANOMALY_PROB = 0.01          # Random anomaly injection probability per vehicle tick.

# Vehicle counts.
NUM_REAL_VEHICLES = 70       # Real source vehicles: 32 summer + 38 winter.
NUM_SYN_VEHICLES = 30        # Synthetic vehicles: 15 Gaussian + 15 degradation.
NUM_VEHICLES = NUM_REAL_VEHICLES + NUM_SYN_VEHICLES

# High-risk VINs used for Power BI map clustering.
# These are mapped from the highest-risk source traces in the preprocessed dataset.
ANOMALY_VEHICLE_VINS = ["VIN-027", "VIN-022", "VIN-064", "VIN-024", "VIN-060"]

# Confirm CRITICAL only after this many consecutive non-normal violations.
CRITICAL_STREAK_THRESHOLD = 3

# Demo map ranges around Seoul.
LOCATION_NORMAL = {
    "lat_min": 37.45,
    "lat_max": 37.65,
    "lon_min": 126.85,
    "lon_max": 127.15,
}

LOCATION_ANOMALY = {
    "lat_min": 37.490,
    "lat_max": 37.510,
    "lon_min": 127.020,
    "lon_max": 127.055,
}

BMW_MODELS = [
    "BMW i3 (60Ah)",
    "BMW i3 (94Ah)",
    "BMW i3s (94Ah)",
    "BMW i3 (120Ah)",
    "BMW i3s (120Ah)",
]

STATUS_MAP = {
    0.0: "NORMAL",
    1.0: "WARNING",
    2.0: "CRITICAL",
    "Normal": "NORMAL",
    "Warning": "WARNING",
    "Danger": "CRITICAL",
}

# Random anomaly injection ranges.
ANOMALY_VOLTAGE_DROP = (10.0, 30.0)   # Voltage drop range in volts.
ANOMALY_TEMP_RISE = (5.0, 12.0)       # Battery temperature rise range in °C.
ANOMALY_CURRENT_EXTRA = (3.0, 8.0)    # Additional discharge-current stress in amps.

# Gaussian synthetic vehicles: VIN-071~085.
GAUSSIAN_COUNT = 15
GAUSSIAN_NOISE_SCALE = 0.08
GAUSSIAN_NOISE_COLS = [
    "voltage", "current", "battery_temp", "ambient_temp",
    "delta_v", "delta_i",
    "joule_heating_stress", "thermal_temperature_70min",
    "thermal_stress",
    "Z_Delta_I", "Z_Delta_V", "Z_Thermal_Stress",
    "BSI", "Z_BSI",
]

# Degradation synthetic vehicles: VIN-086~100.
DEGRADATION_COUNT = 15
DEGRADATION_BASE_VEHICLES = [
    "VehicleA_027", "VehicleA_022", "VehicleB_032",
    "VehicleA_024", "VehicleB_028", "VehicleB_022",
    "VehicleA_014", "VehicleA_002", "VehicleA_001",
    "VehicleA_025",
]

DEGRADATION_VOLTAGE_DROP_MAX = 25.0
DEGRADATION_TEMP_RISE_MAX = 10.0
DEGRADATION_BSI_AMPLIFY_MAX = 4.0
DEGRADATION_BSI_ADD_MAX = 3.0
DEGRADATION_STRESS_RISE_MAX = 8.0

DEGRADATION_WARNING_BSI = 1.2
DEGRADATION_CRITICAL_BSI = 3.0
