# EV-Pulse BMW Playback Simulator

A Python playback simulator that replays preprocessed BMW i3 battery telemetry as a real-time Azure IoT Hub data stream.

This module is part of the EV-Pulse project, an Azure-based EV battery monitoring pipeline for early warning, anomaly detection, SQL storage, and Power BI dashboarding.

```text
Preprocessed BMW CSV
  -> VIN mapping
  -> synthetic vehicle generation
  -> anomaly injection
  -> CRITICAL streak confirmation
  -> JSON payloads
  -> Azure IoT Hub
  -> Stream Analytics
  -> Azure SQL
  -> Power BI
```

## Why this simulator exists

The BMW i3 data is historical CSV telemetry, not live vehicle data. This simulator turns the historical dataset into a realistic streaming source so the cloud pipeline can be tested like a real fleet-monitoring system.

It is designed to validate:

- Azure IoT Hub ingestion
- Stream Analytics input/output queries
- Azure SQL table mappings
- Power BI real-time dashboard visuals
- alert logic for `WARNING` and `CRITICAL` battery states

## Folder structure

```text
Python_Simulator/
  ├── simulator.py
  ├── config.py
  ├── requirements.txt
  ├── README.md
  └── TripAB_all_processed_sampled_60000_window_combined.csv  # local only, not committed
```

> The large CSV file is intentionally excluded from GitHub. Place it in this folder locally when running the simulator.

## Installation

```bash
pip install -r requirements.txt
```

## Quick start

Dry-run mode prints JSON messages locally without sending anything to Azure:

```bash
python3 simulator.py --dry-run
```

Run with an explicit CSV path:

```bash
python3 simulator.py --csv TripAB_all_processed_sampled_60000_window_combined.csv --dry-run
```

Send messages to Azure IoT Hub:

```bash
python3 simulator.py
```

Adjust playback speed:

```bash
python3 simulator.py --interval 0.5
```

Adjust CSV cursor movement:

```bash
python3 simulator.py --rows-per-send 5
```

`--rows-per-send` does not mean “send N messages.” It means each vehicle cursor advances by N CSV rows on every tick. With 0.1-second source sampling, the default value `10` compresses the source data into approximately 1-second playback intervals.

The simulator sends one message per vehicle per tick. With 100 vehicles and a 1-second interval, it emits approximately 100 JSON messages per second.

Stop the simulator with:

```text
Ctrl + C
```

## Azure IoT Hub setup

Update `config.py` with a device connection string from Azure Portal:

```python
IOT_HUB_CONNECTION_STRING = "HostName=<YOUR_IOT_HUB>.azure-devices.net;DeviceId=<DEVICE_ID>;SharedAccessKey=<KEY>"
```

Azure Portal path:

```text
IoT Hub -> Devices -> {device} -> Primary Connection String
```

Never commit a real connection string to GitHub.

## Data flow

```text
Preprocessed CSV: 70 real vehicles / 200,000 rows
  + 15 Gaussian synthetic vehicles      -> VIN-071~085
  + 15 degradation synthetic vehicles   -> VIN-086~100
  -----------------------------------------------------
  Total: 100 simulated vehicles
```

For each vehicle tick, the simulator:

1. Reads the current CSV row.
2. Advances the cursor by `rows_per_send` rows.
3. Optionally injects a rare random anomaly.
4. Applies CRITICAL streak confirmation.
5. Builds a SQL-aligned JSON payload.
6. Prints the payload in dry-run mode or sends it to IoT Hub.

## VIN mapping

| Source vehicle ID | Demo VIN | Description |
|---|---:|---|
| `VehicleA_001~032` | `VIN-001~032` | Summer trip vehicles |
| `VehicleB_001~038` | `VIN-033~070` | Winter trip vehicles |
| `VehicleGaussian_001~015` | `VIN-071~085` | Gaussian-noise synthetic vehicles |
| `VehicleDeg_001~015` | `VIN-086~100` | Degradation synthetic vehicles |

## High-risk map cluster vehicles

These VINs are configured as high-risk vehicles for clearer Power BI map clustering:

| VIN | Source vehicle | Notes |
|---|---|---|
| `VIN-027` | `VehicleA_027` | High-risk source trace |
| `VIN-022` | `VehicleA_022` | High-risk source trace |
| `VIN-064` | `VehicleB_032` | High-risk source trace |
| `VIN-024` | `VehicleA_024` | High-risk source trace |
| `VIN-060` | `VehicleB_028` | High-risk source trace |

## Status logic

The source dataset contains BSI-based pseudo-labels:

| Label | Status |
|---:|---|
| `0` | `NORMAL` |
| `1` | `WARNING` |
| `2` | `CRITICAL` candidate |

To reduce noisy alerts, `CRITICAL` is confirmed only after three consecutive non-normal violations:

```text
Non-normal streak < 3  -> WARNING, is_anomaly = 0
Non-normal streak >= 3 -> CRITICAL, is_anomaly = 1
NORMAL received        -> streak reset
```

## JSON payload example

```json
{
  "vehicle_id": "VIN-027",
  "model_name": "BMW i3 (120Ah)",
  "received_at": "2026-05-22T10:00:00Z",
  "battery_voltage": 382.41,
  "battery_current": -18.25,
  "temperature": 31.7,
  "bsi": 2.184512,
  "status": "WARNING",
  "latitude": 37.502341,
  "longitude": 127.042118,
  "delta_i": 0.0521,
  "delta_v": -0.0184,
  "thermal_stress": 5.2381,
  "z_delta_i": 1.1021,
  "z_delta_v": 0.7732,
  "z_thermal_stress": 1.9234,
  "current_bsi": 2.184512,
  "last_received_at": "2026-05-22T10:00:00Z",
  "is_active": 1,
  "current_region_id": 101,
  "is_anomaly": 0,
  "alert_time": "2026-05-22T10:00:00Z",
  "alert_type": "BATTERY_STRESS",
  "alert_level": "WARNING",
  "message": "VIN-027 WARNING (streak 1/3)",
  "is_sent_teams": 0
}
```

## JSON to SQL mapping

| JSON key | SQL destination |
|---|---|
| `vehicle_id` | `Vehicle.vehicle_id` |
| `model_name` | `VehicleModel.model_name` |
| `received_at` | `Battery_Telemetry.received_at`, `BSI_Feature_Log.received_at` |
| `battery_voltage` | `Battery_Telemetry.battery_voltage` |
| `battery_current` | `Battery_Telemetry.battery_current` |
| `temperature` | `Battery_Telemetry.temperature` |
| `bsi` | `Battery_Telemetry.bsi`, `BSI_Feature_Log.bsi` |
| `status` | `Battery_Telemetry.status`, `Vehicle_Current_Status.status` |
| `latitude`, `longitude` | `Battery_Telemetry`, `Vehicle_Current_Status`, `Alert_Log` |
| `delta_i`, `delta_v` | `BSI_Feature_Log.delta_i`, `BSI_Feature_Log.delta_v` |
| `thermal_stress` | `BSI_Feature_Log.thermal_stress` |
| `z_delta_i`, `z_delta_v`, `z_thermal_stress` | `BSI_Feature_Log` |
| `current_bsi` | `Vehicle_Current_Status.current_bsi` |
| `last_received_at` | `Vehicle_Current_Status.last_received_at` |
| `is_active` | `Vehicle_Current_Status.is_active` |
| `current_region_id` | `Vehicle_Current_Status.current_region_id` |
| `is_anomaly` | Alert insert trigger condition |
| `alert_time`, `alert_type`, `alert_level`, `message`, `is_sent_teams` | `Alert_Log` |

## GitHub safety rules

Do not commit:

- real IoT Hub connection strings
- `.env` files
- `parameters.local.json`
- large CSV files
- generated `.jsonl` files
- `__pycache__` and `.pyc` files
- `.DS_Store`

Recommended `.gitignore` entries are included in this package.

## Portfolio summary

Built a real-time EV telemetry playback simulator using Python and Azure IoT Hub. The simulator replays BMW i3 battery data, generates synthetic fleet vehicles, injects anomaly scenarios, applies CRITICAL streak logic, and emits SQL-aligned JSON for Stream Analytics, Azure SQL, and Power BI monitoring dashboards.
