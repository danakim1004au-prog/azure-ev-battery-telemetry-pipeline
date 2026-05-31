# EV-Pulse — Real-Time EV Battery Anomaly Detection Platform

An end-to-end Azure IoT pipeline that monitors EV battery health in real time,
classifies anomalies using a LightGBM model, and alerts fleet operators through Slack.

Built as a 10-day team MVP. My role: Cloud / DevOps.

---

## System Architecture
<img width="1649" height="954" alt="EV-Pulse_final pipeline" src="https://github.com/user-attachments/assets/3f2e50f2-7ee9-438e-b995-915e1c974463" />


## Repository Structure

| Path | Contents |
|------|----------|
| `infrastructure/` | Bicep IaC — full Azure environment (IoT Hub, SQL, SA, ML Workspace, OpenAI) |
| `infrastructure/main.bicep` | Hand-crafted core 5 resources |
| `infrastructure/template.bicep` | Security-hardened Portal export (103 resources) |
| `.github/workflows/` | GitHub Actions CI/CD — lint → what-if → deploy |
| `Simulator/` | Python playback simulator — 100 vehicles, real-time IoT stream |
| `Simulator/simulator.py` | Main playback engine |
| `Simulator/config.py` | All tunable parameters |

---

## My Contributions (Cloud / DevOps)

- **NASA PCoE Battery Dataset analysis** — preprocessed B0005/B0006/B0007 discharge
  cycles; derived BSI feature weights (Delta_I: 0.4830, Delta_V: 0.2218,
  JHS: 0.1027) via PCA on a Z-score abnormality matrix; defined NORMAL /
  WARNING / CRITICAL thresholds

- **Bicep IaC** — designed `main.bicep` from scratch; security-hardened
  `template.bicep` from Azure Portal export (808 → 103 resources)

- **CI/CD pipeline** — PR-triggered lint + what-if; merge-triggered deploy
  with `needs: validate` dependency chain; Service Principal authentication
  via GitHub Secrets

- **End-to-end pipeline integration** — connected Python Simulator → Azure IoT Hub
  → Stream Analytics → Azure ML using SA JavaScript Functions to invoke the
  LightGBM inference endpoint; resolved a schema contract conflict in `score.py`
  where `@inputschema` / `@outputschema` decorators were required by SA Functions
  but caused Azure ML deployment failure when both were present — debugged and
  fixed jointly with the ML team

- **Python simulator** — v2 architecture separating raw sensor relay from ML
  inference; 100-vehicle fleet with Gaussian and degradation synthetics;
  derived variables (Delta_I, Delta_V, JHS) computed in real time

- **SQL schema** — 7-table `evpulse` schema covering telemetry, BSI features,
  state transitions, and alert logging

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Regions hard-coded in Bicep | Prevents accidental cross-region deployment that would break pipeline latency |
| State transition logic in Azure SQL, not Stream Analytics | SA is stateless; SQL owns persistent state memory |
| Azure ML called via SA JavaScript Functions | Enables inline ML inference within the streaming query without a separate middleware layer |
| HTML dashboard via Azure Function API | Azure Function serves a REST API over Azure SQL; no Power BI dependency |
| BSI as anomaly score | Weighted composite of Delta_I (0.4830), Delta_V (0.2218), JHS (0.1027) — weights derived from NASA PCoE dataset PCA |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| IaC | Azure Bicep · GitHub Actions |
| Ingestion | Azure IoT Hub (MQTT/HTTPS) |
| Stream processing | Azure Stream Analytics |
| SA → ML integration | SA JavaScript Functions → Azure ML endpoint |
| ML inference | Azure ML · LightGBM (3-class: NORMAL / WARNING / CRITICAL) |
| Storage | Azure SQL Database (S0) |
| Dashboard API | Azure Function (REST API → Azure SQL) |
| Dashboard UI | HTML / CSS / JavaScript |
| Alerting | Logic Apps → Slack webhook |
| Chatbot | Azure OpenAI GPT-4o-mini (RAG · Text-to-SQL) |
| Simulator | Python 3.9+ · pandas · numpy · azure-iot-device |

---

## Sub-READMEs

- [`infrastructure/README.md`](infrastructure/README.md) — Bicep IaC, CI/CD pipeline, deployment guide
- [`Simulator/README.md`](Simulator/README.md) — Simulator architecture, payload schema, quick start
