# EV-Pulse — Infrastructure as Code (Bicep)

> Manages the entire Azure infrastructure for the EV-Pulse battery anomaly detection system as code.  
> **This single repository can fully reproduce the EV-Pulse Azure environment from scratch.**

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        EV-Pulse Pipeline                        │
│                                                                 │
│  Vehicle Simulator              Azure Cloud                     │
│  ┌──────────────┐              ┌──────────────────────────────┐ │
│  │ VIN-001      │──MQTT/HTTPS─▶│  IoT Hub                     │ │
│  │ VIN-002      │              │  evpulse-iothub              │ │
│  │ VIN-003      │              └──────────┬───────────────────┘ │
│  └──────────────┘                         │ Event Stream        │
│                                           ▼                     │
│                              ┌──────────────────────────────┐   │
│                              │  Stream Analytics Job        │   │
│                              │  evpulse-sa-job              │   │
│                              │                              │   │
│                              │  · Moving Average (μ/σ)      │   │
│                              │  · Z-Score calculation       │   │
│                              │  · Raw telemetry routing     │   │
│                              └──────────┬───────────────────┘   │
│                                         │ SQL Output            │
│                                         ▼                       │
│                              ┌──────────────────────────────┐   │
│                              │  Azure SQL Database          │   │
│                              │  evpulse-db                  │   │
│                              │                              │   │
│                              │  · telemetry (raw data)      │   │
│                              │  · baseline (μ/σ per VIN)    │   │
│                              │  · state_log (status history)│   │
│                              │  · NORMAL/WARNING/CRITICAL   │   │
│                              │    state transition logic    │   │
│                              └──────┬───────────┬───────────┘   │
│                                     │           │               │
│                          CRITICAL   │           │ Query         │
│                          detection  │           │               │
│                          (every 30s)│           │               │
│                                     ▼           ▼               │
│                    ┌──────────────────┐  ┌──────────────────┐   │
│                    │  Logic Apps      │  │  Power BI        │   │
│                    │  evpulse-logic   │  │  Dashboard       │   │
│                    │  -app            │  │  (real-time)     │   │
│                    └────────┬─────────┘  └──────────────────┘   │
│                             │ Webhook                           │
│                             ▼                                   │
│                    ┌──────────────────┐  ┌──────────────────┐   │
│                    │  Microsoft Teams │  │  Azure OpenAI    │   │
│                    │  #ev-pulse-alerts│  │  Text-to-SQL Bot │   │
│                    └──────────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────────┘

[ML Layer]
  Azure ML Workspace → LightGBM anomaly detection model training & deployment
  NASA Battery Dataset → BSI weight derivation
  BMW i3 Dataset      → Real-vehicle μ/σ parameter extraction

[Production Model]
  Endpoint   : ev-anomaly-endpoint-6403dedf
  Deployment : purple2
  Model      : ev-lgbm-inference-artifact  v8
  Redeploy   : see infrastructure/ml-deployment-purple2.yml
```

---

## CI/CD Pipeline

```
┌──────────────────────────────────────────────────────────────┐
│                  GitHub Actions Workflow                      │
│                                                              │
│  Local Dev                    GitHub                 Azure   │
│  ┌─────────┐   git push    ┌─────────┐            ┌───────┐  │
│  │ Bicep   │──────────────▶│ feature │            │       │  │
│  │ edit    │  (open PR)    │ branch  │            │  RG   │  │
│  └─────────┘               └────┬────┘            │       │  │
│                                 │ PR trigger       │       │  │
│                                 ▼                  │       │  │
│                          ┌─────────────┐           │       │  │
│                          │  validate   │           │       │  │
│                          │  ─────────  │           │       │  │
│                          │ 1.az bicep  │           │       │  │
│                          │   build     │           │       │  │
│                          │   (Lint)    │           │       │  │
│                          │ 2.what-if   │──────────▶│ drift │  │
│                          │  (preview)  │  read-only│ check │  │
│                          └──────┬──────┘  query    │       │  │
│                                 │                  │       │  │
│                   merge to main │                  │       │  │
│                          ┌──────▼──────┐           │       │  │
│                          │   deploy    │           │       │  │
│                          │  ─────────  │           │       │  │
│                          │ needs:      │           │       │  │
│                          │  validate   │           │       │  │
│                          │             │──────────▶│ live  │  │
│                          │ az deploy   │  deploy   │ deploy│  │
│                          │ group create│           │  done │  │
│                          └─────────────┘           └───────┘  │
│                                                              │
│  Trigger conditions                                          │
│  · push to main        → validate + deploy (ordered)        │
│  · pull_request        → validate only (what-if preview)    │
│  · workflow_dispatch   → manual trigger button              │
│  · paths: infrastructure/** → runs only on infra changes   │
└──────────────────────────────────────────────────────────────┘
```

### Pipeline Security Design

| Item | Approach |
|------|----------|
| Azure authentication | Service Principal (`AZURE_CREDENTIALS`) stored in GitHub Secrets |
| Secret injection | GitHub Secrets → `--parameters` runtime injection |
| Secret exposure | No connection strings or passwords in source code |
| Deployment tracking | `--name deploy-${{ github.sha }}` — deployment history keyed by commit hash |

---

## File Structure

```
infrastructure/
├── main.bicep              # Core 5 pipeline resources (hand-crafted)
│                           #   IoT Hub / SQL Server+DB / Stream Analytics
│                           #   Storage Account / Logic Apps
├── template.bicep          # Full snapshot based on Azure Portal export
│                           #   Includes ML Workspace, OpenAI, Key Vault, etc.
│                           #   Secrets removed and comments added
├── parameters.json         # Parameter template (YOUR_* placeholders)
├── parameters_local.json   # Actual values (gitignored — never commit)
├── .gitignore
└── README.md
.github/
└── workflows/
    └── infra-deploy.yml    # CI/CD pipeline definition
```

---

## Region Lock Strategy

| Resource | Reason |
|----------|--------|
| IoT Hub, Stream Analytics, SQL, Logic Apps | Co-located to minimize data pipeline latency |
| Azure OpenAI (`evpulse-azoai`) | Deployed in a region where gpt-4o-mini is available |

> Regions are **intentionally hard-coded** rather than exposed as parameters.  
> This design decision eliminates the risk of accidental cross-region deployments that would break the pipeline architecture.

---

## Deployment Guide

### Prerequisites

```bash
# Log in to Azure CLI
az login

# Create resource group (one-time setup)
az group create --name evpulse-rg --location <your-region>
```

### 1. Prepare Parameter File

```bash
cp parameters.json parameters_local.json
# Open parameters_local.json and replace YOUR_* placeholders with real values
# This file is excluded from Git tracking via .gitignore — never commit it
```

### 2. Preview Changes (Dry-run)

```bash
az deployment group what-if \
  --resource-group evpulse-rg \
  --template-file template.bicep \
  --parameters @parameters_local.json
```

### 3. Deploy

> Use `main.bicep` for iterative development (5 core resources only).  
> Use `template.bicep` for full environment rebuild (includes ML Workspace, OpenAI, Key Vault).  
> Run only one of the two commands per deployment — they are not meant to be executed together.

```bash
# Option A — main.bicep: core 5 pipeline resources (IoT Hub, SQL, SA, Storage, Logic Apps)
az deployment group create \
  --resource-group evpulse-rg \
  --template-file main.bicep \
  --parameters @parameters_local.json

# Option B — template.bicep: full snapshot including ML Workspace, OpenAI, and Key Vault
az deployment group create \
  --resource-group evpulse-rg \
  --template-file template.bicep \
  --parameters @parameters_local.json
```

### 4. Verify Deployment

```bash
# List deployment history
az deployment group list \
  --resource-group evpulse-rg \
  --output table

# Inspect deployment outputs (IoT Hub name, SQL FQDN, etc.)
az deployment group show \
  --resource-group evpulse-rg \
  --name <deployment-name> \
  --query properties.outputs
```

---

## GitHub Actions Setup

### Required GitHub Secrets

Register under: Repository → Settings → Secrets and variables → Actions

| Secret Name | Description |
|-------------|-------------|
| `AZURE_CREDENTIALS` | Full Service Principal JSON |
| `AZURE_RG` | `evpulse-rg` |
| `TENANT_ID` | Azure Tenant ID |
| `SUBSCRIPTION_ID` | Azure Subscription ID |
| `KV_OBJECT_ID` | Key Vault Object ID |
| `IOTHUB_CONNECTION_STRING` | IoT Hub connection string |
| `STORAGE_CONTAINER_PATH` | Storage container path |
| `SQL_ADMIN_PASSWORD` | SQL Server administrator password |

### Create Service Principal

```bash
az ad sp create-for-rbac \
  --name "evpulse-github-actions" \
  --role contributor \
  --scopes /subscriptions/{SUBSCRIPTION_ID}/resourceGroups/evpulse-rg \
  --json-auth
```

Paste the full JSON output as the value of the `AZURE_CREDENTIALS` secret.

---

## Security Hardening (vs. Azure Portal Export)

Changes applied relative to the raw Azure Portal export:

| Item | Before | After |
|------|--------|-------|
| `tenantId` | Hard-coded | `@secure() param tenantId` |
| `objectId` | Hard-coded | `@secure() param keyVaultObjectId` |
| `subscriptionId` | Hard-coded (6 locations) | `param subscriptionId` reference |
| `sqlAdminPassword` | Not declared | `@secure() @minLength(8) param sqlAdminPassword` |
| Connection strings | Hard-coded | GitHub Secrets → runtime injection |
| Log Analytics default resources | 808 items (exceeds Azure limit) | 103 items (auto-generated entries removed) |

---

## Reproduction File Structure

Even after the resource group is deleted, this repository contains everything needed to fully rebuild the environment.

```
infrastructure/
├── template.bicep              ← Recreates all Azure resources (ML Workspace, IoT Hub, etc.)
├── ml-deployment-purple2.yml   ← Recreates the purple2 ML deployment (model version + compute)
├── parameters.json             ← Parameter template (replace YOUR_* with real values)
└── README.md                   ← This file — full architecture and redeployment guide
```

---

## Full Environment Rebuild Order (after resource group deletion)

### Step 1 — Create resource group

```bash
az group create \
  --name 4dt_team_1 \
  --location <your-region>
```

### Step 2 — Deploy all Azure infrastructure (template.bicep)

```bash
az deployment group create \
  --resource-group 4dt_team_1 \
  --template-file infrastructure/template.bicep \
  --parameters @infrastructure/parameters.json \
    tenantId="YOUR_TENANT_ID" \
    keyVaultObjectId="YOUR_OBJECT_ID" \
    subscriptionId="YOUR_SUBSCRIPTION_ID" \
    sqlAdminPassword="YOUR_SQL_PASSWORD"
```

> IoT Hub, SQL, Stream Analytics, ML Workspace, Function App, Azure Bot, and all other resources are provisioned with this single command.

### Step 3 — Redeploy the production ML model (purple2)

```bash
# Install the ML extension (first time only)
az extension add --name ml

# Recreate the purple2 deployment
az ml online-deployment create \
  --file infrastructure/ml-deployment-purple2.yml \
  --workspace-name ev-modeling-ML \
  --resource-group 4dt_team_1 \
  --all-traffic
```

| Item | Value |
|------|-------|
| Endpoint | `ev-anomaly-endpoint-6403dedf` |
| Deployment | `purple2` |
| Model | `ev-lgbm-inference-artifact:8` |
| Algorithm | LightGBM (BSI-based anomaly detection) |
| Instance | `Standard_DS2_v2` × 1 |

> ML Online Deployments are managed outside Bicep because
> `Microsoft.MachineLearningServices/onlineDeployments` requires the registered model
> to exist before the deployment resource can be created — a sequencing constraint that
> cannot be reliably expressed in a single Bicep template. The YAML file captures all
> settings so the deployment can be reproduced deterministically with one CLI command.

---

## Deployed Resources (main.bicep)

| Resource | Name Pattern | Role |
|----------|-------------|------|
| IoT Hub | `evpulse-iothub-{env}` | Receive vehicle telemetry |
| SQL Server | `evpulse-sqlserver-{env}` | Store telemetry and status data |
| SQL Database | `evpulse-db-{env}` | S0 / 10 DTU |
| Stream Analytics | `evpulse-sa-job-{env}` | Real-time Z-score calculation and telemetry routing |
| Storage Account | `evpulsestorage{env}` | Logic Apps runtime storage |
| Logic Apps | `evpulse-logic-app-{env}` | CRITICAL alert → Teams notification |
