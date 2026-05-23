// ============================================================
// EV-Pulse — Azure Infrastructure as Code
// Author  : Dana Kim  |  Team : 4DT Team 1
// Purpose : Reproducible deployment of EV-Pulse core monitoring pipeline
//
// [File Responsibilities]
//   Hand-crafted Bicep file defining the 5 core pipeline resources from scratch.
//   Managed separately from template.bicep (Azure Portal export snapshot).
//   All resource dependencies, parameter design, and tagging strategy
//   were explicitly architected rather than auto-generated.
//
// [Region Lock Strategy — Explicit Location Locking]
//   The entire real-time pipeline (IoT Hub · Stream Analytics · SQL · Logic Apps)
//   is pinned to koreacentral. This is intentionally NOT exposed as a parameter:
//   → Cross-region placement introduces inter-service latency and breaks the architecture.
//   → Hard-coding the region eliminates accidental misdeployment by human error.
//   (Azure OpenAI is pinned to eastus for gpt-4o-mini availability — see template.bicep)
//
// [Deploy]
//   az deployment group create \
//     --resource-group evpulse-rg \
//     --template-file main.bicep \
//     --parameters @parameters.json
// ============================================================

// ── Parameters ──────────────────────────────────────────────

@description('Deployment environment — dev/staging/prod')
@allowed(['dev', 'staging', 'prod'])
param environment string = 'dev'

// [Region Lock] koreacentral — minimizes pipeline latency and prevents misdeployment.
// Declared as a parameter with a fixed default value.
// Changing this affects the entire pipeline; update only after team alignment.
@description('Azure region — pinned to koreacentral to minimize pipeline latency')
param location string = 'koreacentral'

@description('Project prefix — prepended to every resource name as a unique identifier')
param projectPrefix string = 'evpulse'

@description('IoT Hub SKU — S1: team project / F1: free tier for personal testing')
@allowed(['F1', 'S1'])
param iotHubSku string = 'S1'

@description('Azure SQL administrator login name')
param sqlAdminLogin string = 'sqluser'

@description('Azure SQL administrator password')
@secure()
param sqlAdminPassword string

@description('Stream Analytics Streaming Units — 1 is the minimum and lowest cost')
param saStreamingUnits int = 1

// ── Variables ───────────────────────────────────────────────
// All resource names follow a consistent prefix + environment convention.
// This allows dev and prod environments to coexist in the same subscription
// without naming conflicts.

var iotHubName         = '${projectPrefix}-iothub-${environment}'
var sqlServerName      = '${projectPrefix}-sqlserver-${environment}'
var sqlDbName          = '${projectPrefix}-db-${environment}'
var saJobName          = '${projectPrefix}-sa-job-${environment}'
var logicAppName       = '${projectPrefix}-logic-app-${environment}'
var storageAccountName = '${projectPrefix}storage${environment}'  // lowercase alphanumeric only

// ── Resource 1: IoT Hub ─────────────────────────────────────
// Role: Receives battery telemetry from the vehicle simulator (VIN-001~100)
// Dependencies: None — top-level ingestion entry point of the pipeline
// Design decision: partitionCount=2 → dedicated Stream Analytics consumer group

resource iotHub 'Microsoft.Devices/IotHubs@2021-07-02' = {
  name: iotHubName
  location: location
  sku: {
    name: iotHubSku
    capacity: 1
  }
  properties: {
    eventHubEndpoints: {
      events: {
        retentionTimeInDays: 1
        partitionCount: 2          // Reserved partition for Stream Analytics consumer group
      }
    }
    routing: {
      fallbackRoute: {
        name: '$fallback'
        source: 'DeviceMessages'
        condition: 'true'
        endpointNames: ['events']
        isEnabled: true
      }
    }
    cloudToDevice: {
      maxDeliveryCount: 10
      defaultTtlAsIso8601: 'PT1H'
    }
    minTlsVersion: '1.2'          // Security: enforce TLS 1.2 minimum
    disableLocalAuth: false
  }
  tags: {
    project: 'ev-pulse'
    environment: environment
    role: 'ingestion'             // Tag strategy: role tag makes resource purpose explicit
  }
}

// ── Resource 2: Azure SQL Server ────────────────────────────
// Role: Persists telemetry, per-VIN baselines (μ/σ), and state history (state_log)
// Dependencies: None — SQL DB and firewall rules depend on this server
// Design decision: S0 (10 DTU) — sufficient for a 10-day MVP, keeps cost ~$5/month

resource sqlServer 'Microsoft.Sql/servers@2022-05-01-preview' = {
  name: sqlServerName
  location: location
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword  // @secure() param — no value in source code
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
  tags: {
    project: 'ev-pulse'
    environment: environment
    role: 'storage'
  }
}

// SQL Database — child resource with explicit parent dependency
resource sqlDb 'Microsoft.Sql/servers/databases@2022-05-01-preview' = {
  parent: sqlServer              // parent declaration ensures sqlServer is created first
  name: sqlDbName
  location: location
  sku: {
    name: 'S0'
    tier: 'Standard'
  }
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    maxSizeBytes: 268435456000   // 250 GB
  }
  tags: {
    project: 'ev-pulse'
    environment: environment
    role: 'storage'
  }
}

// SQL Firewall — allows internal Azure services (Stream Analytics, Logic Apps) to connect.
// IP range 0.0.0.0–0.0.0.0 is the Azure-standard pattern for allowing Azure-internal access only.
resource sqlFirewallAzureServices 'Microsoft.Sql/servers/firewallRules@2022-05-01-preview' = {
  parent: sqlServer
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// ── Resource 3: Stream Analytics Job ────────────────────────
// Role: Consumes IoT Hub events → computes moving average → calculates AnomalyScore → writes to SQL
// Dependencies: The job resource itself can be created independently.
//   Input/output bindings (IoT Hub connection, SQL output) require connection strings
//   and are configured separately via the portal or CLI — intentionally excluded from Bicep for security.
// Note: Query logic is maintained in /sql/sa_query.sql

resource streamAnalyticsJob 'Microsoft.StreamAnalytics/streamingjobs@2021-10-01-preview' = {
  name: saJobName
  location: location
  properties: {
    sku: {
      name: 'Standard'
    }
    eventsOutOfOrderPolicy: 'Adjust'   // Reorder out-of-sequence events instead of dropping them
    outputErrorPolicy: 'Stop'          // Stop job on output error to prevent silent data loss
    eventsOutOfOrderMaxDelayInSeconds: 5
    eventsLateArrivalMaxDelayInSeconds: 5
    dataLocale: 'en-US'
    compatibilityLevel: '1.2'
    jobType: 'Cloud'
  }
  tags: {
    project: 'ev-pulse'
    environment: environment
    role: 'processing'
    note: 'I/O configured separately — see /sql/sa_query.sql'
  }
}

// ── Resource 4: Storage Account ─────────────────────────────
// Role: Runtime storage for Logic Apps (workflow state, execution history)
// Dependencies: Logic Apps depends on this storage account
// Design decision: Standard_LRS — single-region replication, lowest cost for MVP

resource storageAccount 'Microsoft.Storage/storageAccounts@2022-09-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false   // Security: block public blob access
    supportsHttpsTrafficOnly: true
    accessTier: 'Hot'
  }
  tags: {
    project: 'ev-pulse'
    environment: environment
    role: 'runtime-storage'
  }
}

// ── Resource 5: Logic Apps ───────────────────────────────────
// Role: Polls SQL every 30 seconds for CRITICAL status → sends alert to Teams ev-pulse-alerts channel
// Dependencies: storageAccount (runtime), sqlDb (trigger polling target)
// Design decision: Logic Apps chosen over Function App
//   → No custom code required for Teams Webhook integration
//   → Minimizes development and deployment overhead within the 10-day MVP (ADR Q1)
// Note: Workflow definition (triggers and actions) will be reflected here after portal configuration

resource logicApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: logicAppName
  location: location
  properties: {
    state: 'Enabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {}
      actions: {}
    }
    parameters: {}
  }
  tags: {
    project: 'ev-pulse'
    environment: environment
    role: 'alerting'
    trigger: 'SQL CRITICAL state (30s interval)'
    action: 'Teams webhook → ev-pulse-alerts'
  }
  dependsOn: [
    storageAccount   // Provision runtime storage before Logic Apps
    sqlDb            // Provision trigger target DB before Logic Apps
  ]
}

// ── Outputs ─────────────────────────────────────────────────
// Expose values needed for post-deployment simulator setup and Stream Analytics I/O configuration

output iotHubName string = iotHub.name
output iotHubResourceId string = iotHub.id

output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output sqlDbName string = sqlDb.name

output saJobName string = streamAnalyticsJob.name
output logicAppName string = logicApp.name

output deploymentSummary object = {
  author: 'Dana Kim'
  environment: environment
  location: location
  resources: [
    'IoT Hub (${iotHubSku})'
    'SQL Server + Database (S0 / 10DTU)'
    'Stream Analytics Job (SU: ${saStreamingUnits})'
    'Storage Account (Standard_LRS)'
    'Logic Apps (CRITICAL → Teams)'
  ]
}
