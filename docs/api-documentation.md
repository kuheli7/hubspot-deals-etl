# HubSpot Deals ETL - API Documentation

REST API of the **hubspot-deals-etl** service: start and control deal extractions, monitor progress, and read the extracted deals.

## 📋 Table of Contents
1. [Overview](#-overview)
2. [Authentication](#-authentication)
3. [Base URLs](#-base-urls)
4. [Common Response Formats](#-common-response-formats)
5. [Scan Endpoints](#-scan-endpoints)
6. [Results Endpoints](#-results-endpoints)
7. [Credential Endpoint](#-credential-endpoint)
8. [Health, Stats & Maintenance](#-health-stats--maintenance)
9. [Error Handling](#️-error-handling)
10. [Examples](#-examples)

---

## 🔍 Overview

Extractions are **asynchronous scans**. `POST /scan/start` returns `202 Accepted` immediately; the deal extraction runs in the background. Clients poll `/scan/{scanId}/status` and read `/results/{scanId}/result` once the scan is `completed`.

### API Version
- **Version**: 1.0.0
- **Prefix**: `/api/v1`
- **Content-Type**: `application/json`
- **OpenAPI / Swagger UI**: `/docs/` (spec at `/api/v1/swagger.json`)

### Key Features
- Asynchronous deal extraction from HubSpot CRM v3 into PostgreSQL (dlt)
- Checkpoints committed every N pages, with pause / resume / cancel and crash recovery
- Per-tenant isolation (`organizationId` → own PostgreSQL schema)
- Paginated results API filtered to the rows of a scan
- Strict input validation with descriptive `400` errors

### Scan lifecycle
```
             ┌──────── pause ────────┐
             ▼                       │
pending → running ──────────────► completed
             │  ▲        resume       
             │  └──── paused ◄────┘ (POST /pause, then POST /resume → resuming → running)
             ├──► failed      (HubSpot / database error; errorMessage set) → resume once HubSpot recovers
             ├──► cancelled   (POST /cancel)
             └──► crashed     (no heartbeat; POST /maintenance/detect-crashed) → resume
```

| Status | Meaning |
|---|---|
| `pending` | Job created, extraction not started yet |
| `running` | Pages are being extracted and loaded |
| `paused` | Stopped at a page boundary; all fetched pages are loaded and checkpointed |
| `resuming` | Resume accepted; continues from the last checkpoint |
| `completed` | All pages loaded; results available |
| `failed` | Stopped by an error; see `errorMessage`. Resumable from its last checkpoint |
| `cancelled` | Stopped by the user; pages loaded before cancellation remain |
| `crashed` | Marked by crash detection after a missed heartbeat; resumable |

---

## 🔐 Authentication

The service API itself does not authenticate callers. It is meant to run on an internal network or behind an API gateway, which should add caller authentication in production.

**HubSpot credentials** are supplied per scan in the request body:

### Required Credentials
| Field | Description |
|---|---|
| `config.auth.accessToken` | HubSpot private app access token (`pat-...`) |

### Required Permissions (HubSpot scopes)
- `crm.objects.deals.read`

### Authentication Headers
None are required by the service. The token is sent to HubSpot as `Authorization: Bearer <token>`, stored encrypted, and never echoed back: `config.auth` is always shown as `"***redacted***"`.

---

## 🌐 Base URLs

### Development
```
http://localhost:5200
```

### Staging
```
http://localhost:5201
```

### Production
```
http://localhost:5202
```

### Swagger Documentation
```
http://localhost:5200/docs/
```

All endpoints below are relative to `{baseUrl}/api/v1` except `GET /health` and `GET /docs/`.

### Guideline path aliases
The paths used in the GreenTree API test guideline (TEST-GUIDELINES-V1) are served as aliases of the same endpoints and return identical responses:

| Guideline path | Same as |
|---|---|
| `GET /api/v1/scan/status/{scanId}` | `GET /api/v1/scan/{scanId}/status` |
| `GET /api/v1/scan/result/{scanId}` | `GET /api/v1/results/{scanId}/result` |
| `POST /api/v1/scan/cancel/{scanId}` | `POST /api/v1/scan/{scanId}/cancel` |
| `DELETE /api/v1/scan/remove/{scanId}` | `DELETE /api/v1/scan/{scanId}/remove` |
| `POST /api/v1/scan/pause/{scanId}`, `POST /api/v1/scan/resume/{scanId}` | `.../{scanId}/pause`, `.../{scanId}/resume` |
| `GET /api/v1/jobs/jobs` | `GET /api/v1/scan/list` |
| `GET /api/v1/jobs/statistics` | `GET /api/v1/scan/statistics` |

---

## 📊 Common Response Formats

### Success Response
```json
{
  "success": true,
  "data": { }
}
```

### Error Response (Validation)
```json
{
  "success": false,
  "message": "Configuration validation failed: {'config': {'type': {0: ['Must be one of: deal.']}}}",
  "error": "Configuration validation failed: {'config': {'type': {0: ['Must be one of: deal.']}}}",
  "validation_errors": {
    "config": { "type": { "0": ["Must be one of: deal."] } }
  }
}
```

### Error Response (Application Logic)
```json
{
  "success": false,
  "message": "Scan not completed. Current status: paused",
  "error": "Scan not completed. Current status: paused"
}
```

### Pagination Response
```json
{
  "pagination": {
    "total": 5,
    "limit": 2,
    "offset": 0,
    "hasMore": true,
    "totalPages": 3
  }
}
```

---

## 🔍 Scan Endpoints

### 1. Start Extraction

**`POST /api/v1/scan/start`**

#### Request Body
```json
{
  "config": {
    "scanId": "hubspot-deals-scan-001",
    "organizationId": "org-12345",
    "type": ["deal"],
    "auth": {
      "accessToken": "pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    },
    "filters": {
      "properties": ["hs_tcv", "hs_manual_forecast_category"],
      "archived": false,
      "pageSize": 100,
      "checkpointInterval": 10
    }
  }
}
```

#### Parameters
| Field | Type | Required | Rules | Description |
|---|---|---|---|---|
| `config.scanId` | string | ✅ | 1-255 chars, `[A-Za-z0-9_-]` | Unique scan ID chosen by the caller |
| `config.organizationId` | string | ✅ | 1-40 chars, `[A-Za-z0-9_-]` | Tenant ID; deals go to schema `hubspot_deals_<tenant>` |
| `config.type` | string[] | ✅ | only `"deal"` | Object type to extract |
| `config.auth.accessToken` | string | ✅ | 10-512 chars, no spaces | HubSpot private app token |
| `config.filters.properties` | string[] | ❌ | 1-200 names, `[a-z0-9_]` | Extra deal properties in addition to the 28 defaults |
| `config.filters.archived` | boolean | ❌ | default `false` | `true` extracts archived (deleted) deals instead of active ones |
| `config.filters.pageSize` | integer | ❌ | 1-100, default 100 | Deals per HubSpot request |
| `config.filters.checkpointInterval` | integer | ❌ | 1-1000, default 10 | Pages per loaded and checkpointed batch |

Unknown fields are rejected with `400`.

#### Response
The job record is created before the response is sent, so the scan can be polled immediately.
```json
{
  "success": true,
  "message": "Scan initialization accepted and is now processing in the background.",
  "data": {
    "scanId": "hubspot-deals-scan-001",
    "organizationId": "org-12345",
    "status": "pending",
    "statusUrl": "/api/v1/scan/hubspot-deals-scan-001/status",
    "resultUrl": "/api/v1/results/hubspot-deals-scan-001/result"
  }
}
```

#### Status Codes
| Code | When |
|---|---|
| `202` | Scan accepted; job created with status `pending` |
| `400` | Body is not valid JSON, or fails validation (missing token, bad `type`, invalid IDs, ...) |
| `409` | A scan with this `scanId` already exists, including when identical requests arrive at the same time (only the first one is accepted) |
| `503` | Database unavailable; no job created |
| `500` | Unexpected server error |

> An invalid or under-scoped token is accepted here (`202`); the scan's first step validates it against HubSpot and the scan ends `failed` with the HubSpot message. Use `POST /api/v1/auth/validate` to check a token synchronously.

---

### 2. Get Extraction Status

**`GET /api/v1/scan/{scanId}/status`**

#### Path Parameters
| Name | Description |
|---|---|
| `scanId` | ID used in `POST /scan/start` |

#### Response (Existing Extraction)
```json
{
  "success": true,
  "data": {
    "scanId": "hubspot-deals-scan-001",
    "organizationId": "org-12345",
    "type": "deal",
    "status": "completed",
    "startTime": "2026-10-08T18:36:11.999452+00:00",
    "endTime": "2026-10-08T18:36:13.495025+00:00",
    "lastHeartbeat": "2026-10-08T18:36:13.286856+00:00",
    "recordsExtracted": 5,
    "errorMessage": null,
    "duration": 1.495573,
    "config": {
      "auth": "***redacted***",
      "filters": { "archived": false },
      "type": ["deal"]
    },
    "metadata": {
      "dataset_name": "hubspot_deals_org_12345",
      "table_name": "deals",
      "pipeline_name": "hubspot_deals_extraction",
      "destination": "postgres",
      "source_type": "hubspot_deals",
      "extraction_summary": {
        "total_records": 5,
        "total_pages": 1,
        "batches": 1,
        "page_size": 100,
        "resumed_from_page": null,
        "truncated_at_page_limit": false
      },
      "table_record_counts": { "deals": 5 },
      "indexes": ["idx_deals_tenant", "idx_deals_tenant_stage", "idx_deals_tenant_closedate",
                  "idx_deals_createdate", "idx_deals_lastmodified", "idx_deals_scan"],
      "rate_limit": {
        "interval_milliseconds": "10000",
        "interval_max": "190",
        "interval_remaining": "188"
      },
      "completed_at": "2026-10-08T18:36:13.483449+00:00"
    },
    "checkpointInfo": {
      "latestCheckpoint": {
        "phase": "deals_completed",
        "recordsProcessed": 5,
        "cursor": null,
        "pageNumber": 1,
        "batchSize": 100,
        "checkpoint_data": {
          "batches_completed": 1,
          "finished": true,
          "stop_reason": null,
          "load_ids": ["1791484572.5164623"]
        }
      },
      "progress": null,
      "lastCheckpointAt": "2026-10-08T18:36:13.257381+00:00"
    }
  }
}
```

#### Response (Non-existent Extraction)
```json
{
  "success": false,
  "message": "No scan found with ID: does-not-exist",
  "error": "No scan found with ID: does-not-exist"
}
```

#### Response (Failed - invalid token)
```json
{
  "success": true,
  "data": {
    "scanId": "deals-badtoken-001",
    "status": "failed",
    "recordsExtracted": 0,
    "errorMessage": "HubSpot rejected the access token (401): Authentication credentials not found. ..."
  }
}
```

#### Status Codes
| Code | When |
|---|---|
| `200` | Scan found |
| `404` | Unknown `scanId` |

---

### 3. Pause Extraction

**`POST /api/v1/scan/{scanId}/pause`**

Stops the scan at the next page boundary. Pages already fetched are loaded and a checkpoint (phase `deals_paused`) stores the cursor of the next page.

#### Response
```json
{
  "success": true,
  "scanId": "hubspot-deals-scan-001",
  "message": "Job hubspot-deals-scan-001 has been paused successfully",
  "data": {
    "scanId": "hubspot-deals-scan-001",
    "previousStatus": "running",
    "currentStatus": "paused",
    "pausedAt": "2026-10-08T18:36:16.217476+00:00",
    "progress": { "recordsProcessed": 1, "currentPage": 1, "phase": "deals_batch_committed" }
  }
}
```

#### Status Codes
`200` paused · `400` scan not running/pending (e.g. already completed) · `404` unknown scan

---

### 4. Resume Extraction

**`POST /api/v1/scan/{scanId}/resume`**

Continues a `paused`, `crashed` or `failed` scan from its last committed checkpoint (a crashed scan without a checkpoint restarts from page 1). The previous `errorMessage` is cleared.

#### Response
```json
{
  "success": true,
  "scanId": "hubspot-deals-scan-001",
  "message": "Job hubspot-deals-scan-001 is resuming from checkpoint",
  "data": {
    "scanId": "hubspot-deals-scan-001",
    "previousStatus": "paused",
    "currentStatus": "resuming",
    "resumedAt": "2026-10-08T18:36:19.378297+00:00",
    "resumePoint": { "page": 2, "recordsProcessed": 2, "phase": "deals_paused", "cursor": "2" }
  }
}
```

#### Status Codes
`202` resuming · `404` unknown scan · `409` scan is not paused/crashed/failed, or a failed scan has no checkpoint

---

### 5. Cancel Extraction

**`POST /api/v1/scan/{scanId}/cancel`**

#### Response
```json
{
  "success": true,
  "scanId": "hubspot-deals-scan-001",
  "status": "cancelled",
  "message": "Job cancelled successfully"
}
```

#### Status Codes
| Code | When |
|---|---|
| `200` | Cancelled (pages loaded before cancellation stay in the database) |
| `404` | Unknown scan |
| `409` | Scan is already `completed`, `failed` or `cancelled` |

---

### 6. Remove Extraction

**`DELETE /api/v1/scan/{scanId}/remove`**

Deletes the deal rows written by this scan (`WHERE _scan_id = scanId`), the job record and its checkpoints.

#### Response
```json
{
  "success": true,
  "message": "Scan hubspot-deals-scan-001 successfully removed",
  "data": { "scanId": "hubspot-deals-scan-001", "tablesRemoved": 1, "metadataRemoved": true }
}
```

#### Status Codes
`200` removed · `400` scan is still `running`/`pending` (cancel first) · `404` unknown scan

---

### 7. List Extractions

**`GET /api/v1/scan/list?organizationId=org-12345&limit=20&offset=0`** (alias: `GET /api/v1/jobs/jobs`)

| Query | Default | Rules |
|---|---|---|
| `organizationId` | - | filter by tenant |
| `limit` | 20 | 1-100 |
| `offset` | 0 | ≥ 0 |

```json
{
  "success": true,
  "data": {
    "scans": [ { "scanId": "hubspot-deals-scan-001", "status": "completed", "recordsExtracted": 5, "...": "..." } ],
    "pagination": { "total": 1, "limit": 20, "offset": 0, "hasMore": false, "returned": 1 }
  }
}
```

---

### 8. Extraction Statistics

**`GET /api/v1/scan/statistics?organizationId=org-12345`** (alias: `GET /api/v1/jobs/statistics`)

All figures respect the `organizationId` filter. `extraction_time` is measured over completed scans (`endTime - startTime`).

```json
{
  "success": true,
  "data": {
    "total_jobs": 4,
    "status_breakdown": { "pending": 0, "running": 0, "completed": 3, "paused": 0,
                          "failed": 1, "cancelled": 0, "crashed": 0, "resuming": 0 },
    "recent_jobs_7_days": 4,
    "total_records_extracted": 17,
    "extraction_time": {
      "average_seconds": 3.214,
      "min_seconds": 1.48,
      "max_seconds": 7.26,
      "completed_jobs_measured": 3
    },
    "organization_filter": null,
    "generated_at": "2026-10-08T18:38:19.237929+00:00"
  }
}
```

---

## 📦 Results Endpoints

### 1. Get Available Tables

**`GET /api/v1/results/{scanId}/tables`**

```json
{
  "success": true,
  "data": {
    "scanId": "hubspot-deals-scan-001",
    "datasetName": "hubspot_deals_org_12345",
    "tables": [ { "name": "deals", "rowCount": 5, "extractedCount": 5 } ],
    "totalTables": 1
  }
}
```

### 2. Get Extraction Results

**`GET /api/v1/results/{scanId}/result?tableName=deals&limit=100&offset=0`**

#### Path Parameters
| Name | Description |
|---|---|
| `scanId` | A `completed` scan |

#### Query Parameters
| Name | Default | Rules |
|---|---|---|
| `tableName` | `deals` | table in the tenant schema |
| `limit` | 100 | 1-500 |
| `offset` | 0 | ≥ 0 |

#### Response
```json
{
  "success": true,
  "data": {
    "scanId": "hubspot-deals-scan-001",
    "tableName": "deals",
    "records": [
      {
        "id": "18374659201",
        "dealname": "Globex - Annual Subscription",
        "amount": 25000.0,
        "dealstage": "presentationscheduled",
        "pipeline": "default",
        "dealtype": "newbusiness",
        "closedate": "2026-11-22T17:00:00+00:00",
        "createdate": "2026-10-08T19:02:11.284000+00:00",
        "hs_lastmodifieddate": "2026-10-08T19:02:14.731000+00:00",
        "description": "Annual subscription for the analytics team.",
        "hs_priority": "medium",
        "hs_object_id": 18374659201,
        "hs_deal_stage_probability": 0.6,
        "hs_is_closed": false,
        "hs_is_closed_won": false,
        "archived": false,
        "created_at": "2026-10-08T19:02:11.284000+00:00",
        "updated_at": "2026-10-08T19:02:14.731000+00:00",
        "_extracted_at": "2026-10-08T19:05:40.112233+00:00",
        "_scan_id": "hubspot-deals-scan-001",
        "_tenant_id": "org-12345",
        "_page_number": 1,
        "_source_service": "hubspot_deals",
        "_dlt_load_id": "1791484572.5164623",
        "_dlt_id": "vDqnzS3ZVJUMcA"
      }
    ],
    "pagination": { "total": 5, "limit": 100, "offset": 0, "hasMore": false, "totalPages": 1 },
    "availableTables": ["deals"],
    "columns": ["id", "dealname", "amount", "..."]
  }
}
```

Records are ordered by `_extracted_at DESC, id` and limited to rows written by this scan.

#### Status Codes
| Code | When |
|---|---|
| `200` | Results returned |
| `400` | Invalid `limit` / `offset` |
| `404` | Unknown scan |
| `409` | Scan not `completed` yet (e.g. `running`, `paused`) |

---

## 🔑 Credential Endpoint

### Validate a HubSpot Token

**`POST /api/v1/auth/validate`**

Checks a token against HubSpot (`GET /crm/v3/objects/deals?limit=1`) without creating a scan.

```json
{ "accessToken": "pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" }
```

#### Response (valid)
```json
{
  "success": true,
  "data": {
    "valid": true,
    "has_deals_read_scope": true,
    "status_code": 200,
    "message": "Access token is valid and can read deals",
    "rate_limit": { "interval_milliseconds": "10000", "interval_max": "190", "interval_remaining": "189" }
  }
}
```

#### Response (invalid)
```json
{
  "success": false,
  "message": "HubSpot rejected the access token (401): Authentication credentials not found. ...",
  "data": { "valid": false, "has_deals_read_scope": false, "status_code": 401 }
}
```

#### Status Codes
`200` valid · `400` missing/invalid body · `401` rejected by HubSpot · `403` token lacks `crm.objects.deals.read` · `429` HubSpot rate limit · `502` HubSpot unavailable

---

## 🏥 Health, Stats & Maintenance

### 1. Liveness / Readiness

**`GET /health`** (no prefix - used by Docker health checks)

```json
{
  "status": "healthy",
  "service": "hubspot_deals",
  "environment": "development",
  "version": "1.0.0",
  "timestamp": "2026-10-08T18:28:07.560812+00:00",
  "checks": { "database": "ok" }
}
```
`200` healthy · `503` database unreachable (`"status": "unhealthy"`)

### 2. Service Health with Pipeline Details

**`GET /api/v1/health`**
```json
{
  "status": "healthy",
  "timestamp": "2026-10-08T18:28:08.099397",
  "service": "hubspot_deals_pipeline_dev",
  "pipeline": {
    "pipeline_name": "hubspot_deals_extraction",
    "destination_type": "postgres",
    "source_type": "hubspot_deals",
    "database_health": { "healthy": true, "database": "hubspot_deals_data_dev", "host": "postgres_dev" },
    "supports_checkpoints": true
  }
}
```

### 3. Service Statistics
**`GET /api/v1/stats`** - job statistics plus database size, schemas and tables.

### 4. Pipeline Info
**`GET /api/v1/pipeline/info`** - dlt pipeline name, destination and database health.

### 5. Cleanup Old Scans
**`POST /api/v1/maintenance/cleanup`**
```json
{ "daysOld": 7 }
```
```json
{ "success": true, "data": { "cleanedCount": 1, "daysOld": 7 }, "message": "Successfully cleaned up 1 old scan results" }
```
Removes finished job records and their checkpoints older than `daysOld` (1-365). Active scans are never removed.

### 6. Detect Crashed Scans
**`POST /api/v1/maintenance/detect-crashed?timeoutMinutes=10`**

Marks `running` scans whose heartbeat is older than `timeoutMinutes` (1-60) as `crashed`, so they can be resumed.
```json
{ "success": true, "data": { "crashedJobIds": ["hubspot-deals-scan-007"], "crashedCount": 1, "timeoutMinutes": 10 },
  "message": "Detected 1 crashed jobs" }
```

---

## ⚠️ Error Handling

### Error Response Formats

#### Validation Errors (400)
```json
{
  "success": false,
  "message": "Configuration validation failed: {'config': {'organizationId': ['Organization ID can only contain letters, numbers, underscores, and hyphens']}}",
  "validation_errors": { "config": { "organizationId": ["Organization ID can only contain letters, numbers, underscores, and hyphens"] } }
}
```

#### Malformed JSON (400)
```json
{ "success": false, "message": "Request body must be a valid JSON object", "error": "Request body must be a valid JSON object" }
```

#### HubSpot Authentication Errors (401 on `/auth/validate`; `failed` scan otherwise)
```json
{ "success": false, "message": "HubSpot rejected the access token (401): Authentication credentials not found. ..." }
```

#### HubSpot Authorization Errors (403)
```json
{ "success": false, "message": "HubSpot access token is missing a required scope - deal extraction needs crm.objects.deals.read (403): ..." }
```

#### Not Found Errors (404)
```json
{ "success": false, "message": "No scan found with ID: hubspot-deals-scan-999", "error": "No scan found with ID: hubspot-deals-scan-999" }
```

#### Conflict Errors (409)
```json
{ "success": false, "message": "A scan with ID 'hubspot-deals-scan-001' already exists" }
```
```json
{ "success": false, "message": "Cannot cancel job with status: completed" }
```

#### Rate Limit Errors (429)
HubSpot `429` responses are retried inside the scan (see [api-integration.md](api-integration.md#-rate-limits)). A scan only fails on a daily-limit `429` or after the retries are exhausted, with `errorMessage` such as:
```
HubSpot rate limit exceeded (429): You have reached your daily limit.
```

#### Server Errors (500)
```json
{ "success": false, "message": "An unexpected error occurred: <detail>", "error": "<detail>" }
```

### Common Error Codes
| HTTP | Meaning | Typical cause |
|---|---|---|
| `400` | Bad request | invalid JSON, missing/invalid field, unsupported `type`, unsafe IDs, limit out of range |
| `401` | HubSpot rejected token | wrong / revoked token (credential endpoint) |
| `403` | Missing HubSpot scope | private app lacks `crm.objects.deals.read` |
| `404` | Not found | unknown `scanId` |
| `409` | Conflict | duplicate `scanId`, results of an unfinished scan, cancel/resume in the wrong state |
| `429` | HubSpot rate limit | credential endpoint only; scans retry internally |
| `500` | Server error | unexpected exception |
| `502` | Upstream error | HubSpot 5xx during credential validation |
| `503` | Unhealthy | database unavailable (`/health`) |

---

## 📚 Examples

### Complete Extraction Workflow (curl)

#### 1. Start Extraction
```bash
curl -X POST http://localhost:5200/api/v1/scan/start \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "scanId": "hubspot-deals-scan-001",
      "organizationId": "org-12345",
      "type": ["deal"],
      "auth": { "accessToken": "pat-na1-your-token" },
      "filters": { "pageSize": 100, "checkpointInterval": 10 }
    }
  }'
```

#### 2. Monitor Progress
```bash
curl http://localhost:5200/api/v1/scan/hubspot-deals-scan-001/status
```

#### 3. Get Results
```bash
curl "http://localhost:5200/api/v1/results/hubspot-deals-scan-001/result?tableName=deals&limit=50&offset=0"
```

#### 4. Pause / Resume (checkpoint test)
```bash
curl -X POST http://localhost:5200/api/v1/scan/hubspot-deals-scan-001/pause
curl -X POST http://localhost:5200/api/v1/scan/hubspot-deals-scan-001/resume
```

#### 5. Cancel Extraction (if needed)
```bash
curl -X POST http://localhost:5200/api/v1/scan/hubspot-deals-scan-001/cancel
```

#### 6. Remove Extraction (cleanup)
```bash
curl -X DELETE http://localhost:5200/api/v1/scan/hubspot-deals-scan-001/remove
```

### PowerShell Examples

#### Start Extraction
```powershell
$body = @{
  config = @{
    scanId         = "hubspot-deals-scan-001"
    organizationId = "org-12345"
    type           = @("deal")
    auth           = @{ accessToken = $env:HUBSPOT_ACCESS_TOKEN }
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri "http://localhost:5200/api/v1/scan/start" `
  -ContentType "application/json" -Body $body
```

#### Get Status and Results
```powershell
Invoke-RestMethod "http://localhost:5200/api/v1/scan/hubspot-deals-scan-001/status"
(Invoke-RestMethod "http://localhost:5200/api/v1/results/hubspot-deals-scan-001/result?limit=100").data.records |
  Select-Object id, dealname, amount, dealstage, closedate | Format-Table
```

### Python Examples

#### Start Extraction
```python
import os
import requests

BASE = "http://localhost:5200/api/v1"
scan_id = "hubspot-deals-scan-001"

response = requests.post(f"{BASE}/scan/start", json={
    "config": {
        "scanId": scan_id,
        "organizationId": "org-12345",
        "type": ["deal"],
        "auth": {"accessToken": os.environ["HUBSPOT_ACCESS_TOKEN"]},
        "filters": {"pageSize": 100},
    }
})
assert response.status_code == 202, response.json()
```

#### Monitor Progress
```python
import time

while True:
    status = requests.get(f"{BASE}/scan/{scan_id}/status").json()["data"]
    print(status["status"], status["recordsExtracted"])
    if status["status"] in ("completed", "failed", "cancelled"):
        break
    time.sleep(2)

if status["status"] == "failed":
    raise RuntimeError(status["errorMessage"])
```

#### Get Paginated Results
```python
def iter_deals(scan_id, page_size=100):
    offset = 0
    while True:
        data = requests.get(f"{BASE}/results/{scan_id}/result",
                            params={"tableName": "deals", "limit": page_size, "offset": offset}).json()["data"]
        yield from data["records"]
        if not data["pagination"]["hasMore"]:
            return
        offset += page_size

for deal in iter_deals(scan_id):
    print(deal["id"], deal["dealname"], deal["amount"], deal["dealstage"])
```

#### Error Handling
```python
response = requests.post(f"{BASE}/scan/start", json={"config": {"scanId": "bad id!"}})
if response.status_code == 400:
    print(response.json()["validation_errors"])
elif response.status_code == 409:
    print("Scan already exists:", response.json()["message"])
```
