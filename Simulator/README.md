# EV-Pulse BMW Playback Simulator  v2

A Python playback simulator that replays preprocessed BMW EV battery telemetry as a real-time Azure IoT Hub data stream, with vehicles distributed across major Australian cities.

This module is part of the EV-Pulse project — an Azure-based EV battery monitoring pipeline for early anomaly detection, BSI scoring, SQL storage, and dashboard visualisation.

> **Note on location scope:** GPS locations use Australian cities for demonstration
> purposes. The Azure infrastructure is deployed in Korea Central — location data does
> not affect pipeline region.

## Architecture (v2)

```
Preprocessed BMW CSV  (200,000 rows / 70 real vehicles)
  -> VIN mapping
  -> Gaussian synthetic vehicles  (15 vehicles, VIN-071~085)
  -> Degradation synthetic vehicles  (15 vehicles, VIN-086~100)
  -> Raw sensor values + derived variables (Delta_I, Delta_V, JHS)
  -> Azure IoT Hub
  -> Stream Analytics
  -> Azure ML  (Z-score · BSI · Normal / Warning / Critical)
  -> Azure SQL
  -> HTML dashboard
```

### Role separation

| Component | Responsibility |
|---|---|
| **Simulator (this module)** | Raw sensor replay · Delta_I / Delta_V / Joule_Heating_Stress calculation · GPS location |
| **Azure ML** | Z-score normalisation → BSI calculation → status classification |

The simulator does **not** compute BSI, Z-scores, or status labels.
Azure ML receives the raw and derived sensor stream and performs all classification.

---

## Why v2 differs from v1

| Item | v1 (legacy) | v2 (current) |
|---|---|---|
| Columns read from CSV | 20 (BSI, Z-scores, status included) | 5 (raw sensors only) |
| Delta_I / Delta_V | Read from CSV | Calculated in real time: `I(t) - I(t-1)` |
| Joule_Heating_Stress | Read from CSV | Calculated in real time: `I(t)^2 * T(t)` |
| BSI / status | Sent in payload | Not sent — Azure ML computes on arrival |
| CRITICAL streak logic | In simulator (3-consecutive) | Removed — Azure ML responsibility |
| Gaussian noise columns | 15 columns (including BSI, Z-scores) | 4 raw sensor columns only |
| Degradation vehicles | Directly manipulates BSI values | Voltage + temperature only |
| Location mapping | 2 districts | Australian suburbs (suburb-level detail) |
| BMW model names | i3 (legacy) | i4 / iX1 / i7 / i5 / iX |

**Design rationale:** v1 shipped BSI and status labels from the simulator, coupling
classification logic to the data source. v2 moves all inference to Azure ML, making
the simulator a pure sensor relay and the ML pipeline independently testable.

---

## Prerequisites

```
Python  3.9+
azure-iot-device
pandas
numpy
python-dotenv
```

Install with:

```bash
pip install -r requirements.txt
```

---

## Quick start

Print JSON to the console without sending to Azure:

```bash
python3 simulator.py --dry-run
```

Example console output:

```
[LOAD] TripAB_all_processed_sampled_60000_window_combined.csv
[LOAD] Loaded 70 vehicles and 200,000 rows
[AUGMENT] Generated 15 Gaussian synthetic vehicles
[AUGMENT] Generated 15 degradation synthetic vehicles
[START] 100 vehicles / interval=1.0s / rows_per_send=10
[START] Press Ctrl+C to stop

{"vehicle_id": "VIN-027", "model_name": "BMW i4 eDrive40", "received_at": "2026-05-31T10:00:00Z",
 "battery_voltage": 382.4100, "battery_current": -18.2500, "temperature": 31.76, ...
 "region_name": "Sydney CBD, NSW", "is_active": 1}
```

Send messages to Azure IoT Hub:

```bash
python3 simulator.py
```

Use an explicit CSV path:

```bash
python3 simulator.py --csv /path/to/TripAB_all_processed_sampled_60000_window_combined.csv
```

Adjust playback speed:

```bash
# --rows-per-send: CSV cursor advances N rows per vehicle per tick.
# Default 10 = 0.1 s source sampling compressed to 1 s playback.
# Setting 5 halves the playback speed (2 s of source data per second of real time).
python3 simulator.py --interval 0.5 --rows-per-send 5
```

Stop with **Ctrl+C**.

---

## Azure IoT Hub setup

1. Azure Portal → IoT Hub → **Devices** → **+ Add Device**
2. Device ID: `ev-simulator` / Authentication: Symmetric key / Auto-generate keys
3. Click the created device → copy **Primary Connection String**
4. Paste into `config.py`:

```python
IOT_HUB_CONNECTION_STRING = "HostName=xxx.azure-devices.net;DeviceId=ev-simulator;SharedAccessKey=xxx="
```

> ⚠️ Never commit a real connection string to GitHub.

---

## Derived variables

The simulator calculates three variables per vehicle per tick and forwards them to Azure ML:

| Variable | Formula | Unit | BSI weight |
|---|---|---|---|
| `delta_i` | `I(t) - I(t-1)` | A | **0.4830** (highest) |
| `delta_v` | `V(t) - V(t-1)` | V | 0.2218 |
| `joule_heating_stress` | `I(t)² × T(t)` | A²·°C | 0.1027 |

> BSI weights derived from NASA PCoE battery dataset (B0005/B0006/B0007) using PCA on a
> Z-score abnormality matrix — see the weight derivation document for full methodology.

On the first tick per vehicle, `delta_i` and `delta_v` are set to `0.0` (no previous reading available).

---

## Vehicle fleet

```
Real vehicles           70    VIN-001~070  (CSV playback, original sensor values)
Gaussian synthetic      15    VIN-071~085  (8% Gaussian noise on 4 raw sensors)
Degradation synthetic   15    VIN-086~100  (voltage -25 V · temp +10 °C, linearly
                                            applied over full CSV playback duration
                                            via np.linspace(0, 1, n_rows))
──────────────────────────────────────────────────────────────────────────────────
Total                  100
```

### VIN mapping

| Source vehicle ID | Demo VIN | Description |
|---|---|---|
| `VehicleA_001~032` | `VIN-001~032` | Summer trip vehicles |
| `VehicleB_001~038` | `VIN-033~070` | Winter trip vehicles |
| `VehicleGaussian_001~015` | `VIN-071~085` | Gaussian-noise synthetic |
| `VehicleDeg_001~015` | `VIN-086~100` | Progressive degradation |

### Gaussian noise scale — 8%

Noise magnitude is set to **8% of each column's standard deviation** (`std × 0.08`).
Scale factor (8%) was selected to produce realistic sensor jitter without exceeding
the physical clipping bounds (voltage 280–420 V, temperature −5–50 °C).

### High-risk cluster vehicles (Sydney CBD)

| VIN | Source vehicle | Danger ratio |
|---|---|---|
| `VIN-027` | `VehicleA_027` | 6.21% |
| `VIN-022` | `VehicleA_022` | 6.19% |
| `VIN-064` | `VehicleB_032` | 6.19% |
| `VIN-024` | `VehicleA_024` | 6.17% |
| `VIN-060` | `VehicleB_028` | 6.17% |

> **Danger ratio** = proportion of WARNING + CRITICAL rows per source vehicle in the
> BMW i3 dataset, calculated during preprocessing.
> Top-5 vehicles by this metric are assigned to the Sydney CBD cluster (LOCATION_ANOMALY).

---

## Australian city distribution

Normal vehicles (95 of 100) are distributed across cities by risk-weighted random assignment.
High-risk vehicles are pinned to Sydney CBD.

| City | Weight | Notes |
|---|---|---|
| Sydney, NSW | 35% | Anomaly VINs pinned here |
| Melbourne, VIC | 28% | Random assignment |
| Brisbane, QLD | 18% | Random assignment |
| Adelaide, SA | 9% | Random assignment |
| Perth, WA | 4% | Random assignment |
| Gold Coast / Newcastle / Canberra / Hobart / Darwin | 6% combined | Random assignment |

### Suburb coverage and region_id ranges

`_region_from_latlon()` maps each GPS coordinate to a suburb-level name using bounding
boxes. City catch-all boxes prevent any coordinate within a city range from falling back
to the `"Australia"` default.

| region_id range | City |
|---|---|
| 101–115 · 901 | Sydney suburbs · Sydney catch-all |
| 121–132 · 902 | Melbourne suburbs · Melbourne catch-all |
| 141–150 · 903 | Brisbane suburbs · Brisbane catch-all |
| 161–168 · 904 | Adelaide suburbs · Adelaide catch-all |
| 171–178 · 905 | Perth suburbs · Perth catch-all |
| 181–190 | Other cities (Gold Coast, Newcastle, Canberra, Hobart, Darwin, Cairns, Wollongong, Geelong, Townsville) |
| 1 | Australia (final fallback — coordinate outside all boxes) |

> Full suburb coverage ensures the Power BI map reflects realistic fleet distribution
> across Australian cities rather than concentrating all markers in two locations (v1 limitation).

---

## JSON payload (16 fields)

```json
{
  "vehicle_id":           "VIN-027",
  "model_name":           "BMW i4 eDrive40",
  "received_at":          "2026-05-31T10:00:00Z",
  "battery_voltage":      382.4100,
  "battery_current":      -18.2500,
  "temperature":          31.7600,
  "ambient_temp":         22.1000,
  "delta_i":             -0.350000,
  "delta_v":              0.120000,
  "joule_heating_stress": 10537.8720,
  "latitude":             -33.868421,
  "longitude":             151.208905,
  "current_region_id":    101,
  "region_name":          "Sydney CBD, NSW",
  "is_active":            1,
  "last_received_at":     "2026-05-31T10:00:00Z"
}
```

### Payload field types

| Field | Type | Notes |
|---|---|---|
| `vehicle_id`, `model_name`, `received_at`, `region_name`, `last_received_at` | `string` | Generated |
| `battery_voltage`, `battery_current`, `temperature`, `ambient_temp` | `float` | 4 decimal places |
| `delta_i`, `delta_v` | `float` | 6 decimal places; `0.0` on first tick |
| `joule_heating_stress` | `float` | 4 decimal places |
| `latitude`, `longitude` | `float` | 6 decimal places; negative = southern hemisphere |
| `current_region_id` | `int` | 101–115 Sydney · 121–132 Melbourne · 141–150 Brisbane · 161–168 Adelaide · 171–178 Perth · 181–190 other cities · 901–905 city catch-alls |
| `is_active` | `int` | Always `1` |

---

## Transmission rate

| Setting | Value | Meaning |
|---|---|---|
| `SEND_INTERVAL_SEC` | `1.0` | One send cycle per second |
| `ROWS_PER_SEND` | `10` | Cursor advances 10 rows (0.1 s source → 1 s playback) |
| Messages / second | ~100 | One JSON message per vehicle per tick |
| Messages / minute | ~6,000 | 100 vehicles × 60 ticks |

---

## GitHub safety rules

Do not commit:

- Real IoT Hub connection strings
- `.env` files or `parameters.local.json`
- Large CSV files
- Generated `.jsonl` files
- `__pycache__` / `.pyc` / `.DS_Store`
