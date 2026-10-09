# 📋 HubSpot Deals ETL - Integration with the HubSpot CRM API

This document describes the HubSpot CRM API v3 endpoints that the **hubspot-deals-etl** service uses to extract deal records, how it authenticates, paginates, handles rate limits and errors, and which deal properties it extracts.

> Implementation: [`services/hubspot_api_service.py`](../services/hubspot_api_service.py) (HTTP client) and [`services/data_source.py`](../services/data_source.py) (DLT source and transformation).

---

## 📋 Overview

The service reads deals from a HubSpot account with a **private app access token** and loads them into PostgreSQL with [dlt](https://dlthub.com/).

### ✅ **Required Endpoint (Essential)**
| **API Endpoint** | **Purpose** | **Version** | **Required Scope** | **Usage** |
|---|---|---|---|---|
| `GET /crm/v3/objects/deals` | List deals with cursor pagination | CRM v3 | `crm.objects.deals.read` | **Required** - every extraction |

### 🔧 **Optional Endpoints (Used for setup, validation and verification)**
| **API Endpoint** | **Purpose** | **Version** | **Required Scope** | **Used by** |
|---|---|---|---|---|
| `GET /crm/v3/objects/deals/{dealId}` | Read one deal | CRM v3 | `crm.objects.deals.read` | `HubSpotAPIService.get_deal()` |
| `GET /crm/v3/properties/deals` | List every deal property definition (name, label, type) | CRM v3 | `crm.objects.deals.read` | `get_deal_properties()`, `scripts/export_deal_properties.py` |
| `GET /crm/v3/pipelines/deals` | List deal pipelines and their stage IDs | CRM v3 | `crm.objects.deals.read` | `get_deal_pipelines()`, `scripts/create_test_deals.py` |
| `POST /crm/v3/objects/deals` | Create a deal | CRM v3 | `crm.objects.deals.write` | `scripts/create_test_deals.py` only |
| `POST /crm/v3/objects/deals/search` | Filtered search (e.g. modified since) | CRM v3 | `crm.objects.deals.read` | Not used - see [Incremental extraction](#-incremental-extraction-future-option) |

### 🎯 **Recommendation**
The list endpoint returns every property the service needs in a single call per 100 deals. No per-deal follow-up calls are made, so a full extraction of *N* deals costs `ceil(N / 100) + 1` requests (the extra one is the credential check).

> HubSpot also publishes date-versioned CRM paths (for example `/crm/objects/2026-03/deals`). The assignment specifies CRM **v3**, which remains available. The endpoint paths are configurable (`HUBSPOT_DEALS_ENDPOINT`, `HUBSPOT_DEAL_PROPERTIES_ENDPOINT`, `HUBSPOT_DEAL_PIPELINES_ENDPOINT`) so they can be switched without code changes.

---

## 🔐 Authentication Requirements

### **Private App Access Token (Bearer) Authentication**
```http
Authorization: Bearer pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
Accept: application/json
```

Private app tokens are created in HubSpot under **Settings → Integrations → Private Apps** (legacy private apps) and can be rotated or revoked at any time. They are tied to one HubSpot account.

### **Required Scopes**
- **`crm.objects.deals.read`**: read deals, deal properties and deal pipelines. This is the only scope the extraction service needs.
- **`crm.objects.deals.write`**: *only* for `scripts/create_test_deals.py`, which seeds the 5 test deals. Production tokens should not have it.

### **How the service handles the token**
| Concern | Behaviour |
|---|---|
| Source | Sent by the caller in each scan request (`config.auth.accessToken`); never read from the service environment |
| Storage | Encrypted with Fernet (PBKDF2-derived key from `CONFIG_PASSWORD`) inside `jobs.config` so paused, crashed or failed scans can resume |
| Exposure | Redacted (`***redacted***`) in every API response and log line; validation logs show only a masked form (`pat-na1...abcd`) |
| Isolation | Sent per request (not stored on the shared HTTP session), so concurrent scans for different tenants never share credentials |
| Validation | `POST /api/v1/auth/validate` and the first step of every scan call `GET /crm/v3/objects/deals?limit=1` to confirm the token works *and* has the deals read scope |

---

## 🌐 HubSpot API Endpoints

### 🎯 **PRIMARY ENDPOINT (Required for Deal Extraction)**

### 1. **List Deals** - `/crm/v3/objects/deals` ✅ **REQUIRED**

**Purpose**: Page through all deals in the account. This is the only endpoint used during extraction.

**Method**: `GET`

**URL**: `https://api.hubapi.com/crm/v3/objects/deals`

**Query Parameters**:
| Parameter | Type | Default | Description | Service usage |
|---|---|---|---|---|
| `limit` | integer | 10 | Results per page, **maximum 100** | `HUBSPOT_PAGE_SIZE` (default 100) or scan filter `pageSize` (1-100) |
| `after` | string | - | Paging cursor from the previous page's `paging.next.after` | Stored in checkpoints for resume |
| `properties` | comma-separated string | small default set | Properties to return. Unknown names are ignored; known but empty properties return `null` | 28 default properties + scan filter `properties` |
| `propertiesWithHistory` | comma-separated string | - | Return current and historical values | Not used (keeps payload small) |
| `associations` | comma-separated string | - | Object types to return associated IDs for | Not used |
| `archived` | boolean | `false` | `true` returns only archived (deleted) deals | Scan filter `archived` |

**Request Example**:
```http
GET https://api.hubapi.com/crm/v3/objects/deals?limit=100&archived=false&properties=dealname,amount,dealstage,pipeline,dealtype,closedate,createdate,hs_lastmodifieddate,description,hubspot_owner_id
Authorization: Bearer pat-na1-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
Accept: application/json
```

**Response Structure**:
```json
{
  "results": [
    {
      "id": "18374659201",
      "properties": {
        "amount": "25000",
        "closedate": "2026-11-22T17:00:00Z",
        "createdate": "2026-10-08T19:02:11.284Z",
        "dealname": "Globex - Annual Subscription",
        "dealstage": "presentationscheduled",
        "dealtype": "newbusiness",
        "description": "Annual subscription for the analytics team.",
        "hs_lastmodifieddate": "2026-10-08T19:02:14.731Z",
        "hs_object_id": "18374659201",
        "hs_priority": "medium",
        "hubspot_owner_id": null,
        "pipeline": "default"
      },
      "createdAt": "2026-10-08T19:02:11.284Z",
      "updatedAt": "2026-10-08T19:02:14.731Z",
      "archived": false
    }
  ],
  "paging": {
    "next": {
      "after": "18374659202",
      "link": "https://api.hubapi.com/crm/v3/objects/deals?limit=100&after=18374659202"
    }
  }
}
```

**Key facts the implementation relies on**
- **All property values are strings** (or `null`), including numbers, booleans and dates. `transform_deal()` converts them to typed values (see [Deal Properties](#-deal-properties)).
- **Pagination is cursor-based.** `paging` is absent on the last page; that is the stop condition. `paging.next.after` is opaque and must be passed back unchanged.
- `createdAt`, `updatedAt`, `archived` (and `archivedAt` for archived deals) are object-level fields outside `properties`.
- Deal stages and pipelines are returned as **internal IDs** (`presentationscheduled`, `default`), not labels. Stages created in the HubSpot UI get generated IDs rather than readable names; use `GET /crm/v3/pipelines/deals` to map IDs to labels.

**Rate Limit**: shared with all other API calls of the private app - see [Rate Limits](#-rate-limits).

---

## 🔧 **OPTIONAL ENDPOINTS**

> These are not needed for extraction. They support credential checks, test-data setup and verification.

### 2. **Get Deal** - `/crm/v3/objects/deals/{dealId}` 🔧 **OPTIONAL**
```http
GET https://api.hubapi.com/crm/v3/objects/deals/18374659201?properties=dealname,amount,dealstage
Authorization: Bearer pat-na1-...
```
Returns a single deal object with the same shape as an item of `results` above. `404` if the deal does not exist or was archived.

### 3. **List Deal Properties** - `/crm/v3/properties/deals` 🔧 **OPTIONAL**
```http
GET https://api.hubapi.com/crm/v3/properties/deals
Authorization: Bearer pat-na1-...
```
```json
{
  "results": [
    {
      "name": "amount",
      "label": "Amount",
      "type": "number",
      "fieldType": "number",
      "groupName": "dealinformation",
      "description": "The total amount of the deal",
      "hubspotDefined": true,
      "calculated": false,
      "options": []
    }
  ]
}
```
`type` is the storage type (`string`, `number`, `date`, `datetime`, `enumeration`, `bool`, `phone_number`), `fieldType` the UI control (`text`, `textarea`, `number`, `select`, `radio`, `booleancheckbox`, `date`, `calculation_equation`, ...). `scripts/export_deal_properties.py` writes this list for a given account to [`deal-properties.md`](deal-properties.md).

### 4. **List Deal Pipelines** - `/crm/v3/pipelines/deals` 🔧 **OPTIONAL**
```json
{
  "results": [
    {
      "id": "default",
      "label": "Sales Pipeline",
      "stages": [
        { "id": "appointmentscheduled", "label": "Appointment Scheduled", "metadata": { "probability": "0.2" } },
        { "id": "qualifiedtobuy", "label": "Qualified To Buy", "metadata": { "probability": "0.4" } },
        { "id": "presentationscheduled", "label": "Presentation Scheduled", "metadata": { "probability": "0.6" } },
        { "id": "decisionmakerboughtin", "label": "Decision Maker Bought-In", "metadata": { "probability": "0.8" } },
        { "id": "contractsent", "label": "Contract Sent", "metadata": { "probability": "0.9" } },
        { "id": "closedwon", "label": "Closed Won", "metadata": { "probability": "1.0", "isClosed": "true" } },
        { "id": "closedlost", "label": "Closed Lost", "metadata": { "probability": "0.0", "isClosed": "true" } }
      ]
    }
  ]
}
```
The stage IDs above are HubSpot's defaults for the default pipeline; accounts can differ, which is why the test-data script resolves stages by label at runtime.

### 5. **Create Deal** - `POST /crm/v3/objects/deals` 🔧 **TEST DATA ONLY**
```http
POST https://api.hubapi.com/crm/v3/objects/deals
Authorization: Bearer pat-na1-...
Content-Type: application/json

{
  "properties": {
    "dealname": "Acme Corp - Starter Plan",
    "amount": "5000",
    "pipeline": "default",
    "dealstage": "qualifiedtobuy",
    "dealtype": "newbusiness",
    "closedate": "2026-11-07T17:00:00.000Z",
    "description": "Small team starter subscription."
  }
}
```
Requires `crm.objects.deals.write`. Returns `201` with the created deal.

---

## 📊 Data Extraction Flow

```
POST /api/v1/scan/start
        │
        ▼
 1. Validate token ──── GET /crm/v3/objects/deals?limit=1 ──► 401/403 → job "failed" with HubSpot message
        │
        ▼
 2. Resume point ─────── latest committed checkpoint (cursor, page, records) or start from page 1
        │
        ▼
 3. Batch loop (one dlt pipeline.run per batch of N pages, N = checkpointInterval, default 10)
     ├─ before each page: check cancel / pause flags in the jobs table
     ├─ GET /crm/v3/objects/deals?limit=100&after=<cursor>&properties=...
     ├─ transform_deal(): strings → NUMERIC / TIMESTAMPTZ / BOOLEAN / BIGINT, add _extracted_at,
     │                    _scan_id, _tenant_id, _page_number, _source_service
     ├─ dlt merges rows into hubspot_deals_<tenant>.deals on primary key "id"
     └─ after the batch is loaded: commit checkpoint {cursor = paging.next.after, page, records}
        │
        ▼
 4. No paging.next → create indexes, mark job "completed" with recordsExtracted
```

Committing the checkpoint **after** the batch is loaded means the stored cursor never points past data that is not yet in PostgreSQL. A crash, pause or cancel therefore resumes without gaps, and the `merge` write disposition makes re-reading a page harmless.

### 🔁 Incremental extraction (future option)
The list endpoint cannot filter by date, so each scan reads all deals. For large accounts an incremental mode could use `POST /crm/v3/objects/deals/search` with a `hs_lastmodifieddate GTE <last run>` filter. HubSpot's search endpoints have their own stricter limits and do not send the rate-limit headers (per the usage guidelines), and they cap the number of results per query, so the list endpoint was preferred for full extractions.

---

## ⚡ Rate Limits

### **HubSpot limits for privately distributed apps** ([usage guidelines](https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines))
| Product tier | Burst limit (per app) | Daily limit (per account) |
|---|---|---|
| Free and Starter | 100 requests / 10 seconds | 250,000 |
| Professional | 190 requests / 10 seconds | 625,000 |
| Enterprise | 190 requests / 10 seconds | 1,000,000 |
| With API limit increase | 250 requests / 10 seconds | +1,000,000 per increase |

The assignment specifies **150 requests / 10 seconds**, which is the service default (`HUBSPOT_RATE_LIMIT_MAX_REQUESTS=150`, `HUBSPOT_RATE_LIMIT_WINDOW_SECONDS=10`). On a Free/Starter account set it to `100`.

### **Rate-limit response headers**
| Header | Meaning |
|---|---|
| `X-HubSpot-RateLimit-Max` | Requests allowed in the current window |
| `X-HubSpot-RateLimit-Remaining` | Requests left in the current window |
| `X-HubSpot-RateLimit-Interval-Milliseconds` | Window length (10000) |
| `X-HubSpot-RateLimit-Daily` | Requests allowed per day (not sent for OAuth tokens) |
| `X-HubSpot-RateLimit-Daily-Remaining` | Requests left today (not sent for OAuth tokens) |

### **429 response**
```json
{
  "status": "error",
  "message": "You have reached your ten_secondly_rolling limit.",
  "errorType": "RATE_LIMIT",
  "correlationId": "c033cdaa-2c40-4a64-ae48-b4cec88dad24",
  "policyName": "TEN_SECONDLY_ROLLING"
}
```
`policyName` is `TEN_SECONDLY_ROLLING` for the burst limit and `DAILY` for the daily limit.

### **How the service stays within the limits**
1. **Client-side sliding window** (`RateLimiter`): at most 150 requests in any rolling 10 s per scan. Calls block until a slot frees up.
2. **Header guard**: if `X-HubSpot-RateLimit-Remaining` drops to 1 (e.g. other apps share the quota) the client waits one interval before the next call.
3. **429 wait**: waits `Retry-After` if present, otherwise `X-HubSpot-RateLimit-Interval-Milliseconds`, otherwise exponential back-off capped at 10 s. Rate-limit waits do **not** use up the `HUBSPOT_RETRY_ATTEMPTS` (3) retries; they have their own limit of 10 waits per request.
4. **Daily limit**: a `DAILY` 429 is not retried (waiting seconds cannot help). The job fails with the HubSpot message and can be restarted the next day.
5. Every response's rate-limit headers are stored in the scan metadata (`metadata.rate_limit`) for monitoring.

---

## ⚠️ Error Handling

| HubSpot response | Exception | Retried? | Effect on the scan |
|---|---|---|---|
| `400 Bad Request` | `HubSpotAPIError` | No | `failed`, message includes HubSpot's `message` |
| `401 Unauthorized` (missing, invalid, revoked token) | `HubSpotAuthenticationError` | No | `failed` at credential validation: *"HubSpot rejected the access token (401): ..."* |
| `403 Forbidden` (missing scope) | `HubSpotPermissionError` | No | `failed`: *"... deal extraction needs crm.objects.deals.read (403)"* |
| `404 Not Found` | `HubSpotNotFoundError` | No | `failed` (only possible for single-deal reads) |
| `429 TEN_SECONDLY_ROLLING` | `HubSpotRateLimitError` | Yes, waits up to 10 times (not counted as retries) | Continues; fails only if still limited after 10 waits |
| `429 DAILY` | `HubSpotRateLimitError` | No | `failed` with the daily-limit message |
| `5xx` | `HubSpotServerError` | Yes, exponential back-off | Continues; fails after 3 retries, then resumable from the last checkpoint with `POST /scan/{id}/resume` |
| Connection error / timeout (`HUBSPOT_API_TIMEOUT`=30 s) | `HubSpotServerError` | Yes, exponential back-off | Continues; fails after 3 retries |

HubSpot error bodies include a `correlationId`; it is logged with every failed request so it can be quoted to HubSpot support. dlt wraps source exceptions in `PipelineStepFailed`, so the extraction service unwraps the chain and stores the original HubSpot message in `jobs.errorMessage`.

---

## 📑 Deal Properties

### Properties extracted by default
`DEFAULT_DEAL_PROPERTIES` in `hubspot_api_service.py`. Extra properties can be requested per scan with `filters.properties`; they are stored as text columns.

| Internal name | Label | HubSpot type | Stored as (PostgreSQL) |
|---|---|---|---|
| `dealname` | Deal name | string | `varchar` |
| `amount` | Amount | number | `numeric(18,2)` |
| `dealstage` | Deal stage | enumeration (stage ID) | `varchar` |
| `pipeline` | Pipeline | enumeration (pipeline ID) | `varchar` |
| `dealtype` | Deal type | enumeration (`newbusiness`, `existingbusiness`) | `varchar` |
| `closedate` | Close date | datetime | `timestamptz` |
| `createdate` | Create date | datetime | `timestamptz` |
| `hs_lastmodifieddate` | Last modified date | datetime | `timestamptz` |
| `description` | Deal description | string | `varchar` |
| `hubspot_owner_id` | Deal owner | enumeration (owner ID) | `varchar` |
| `hs_object_id` | Record ID | number | `bigint` |
| `hs_priority` | Priority | enumeration (`low`, `medium`, `high`) | `varchar` |
| `hs_deal_stage_probability` | Deal probability | number (0-1) | `double precision` |
| `hs_forecast_amount` | Forecast amount | number | `numeric(18,2)` |
| `hs_projected_amount` | Weighted amount | number | `numeric(18,2)` |
| `amount_in_home_currency` | Amount in company currency | number | `numeric(18,2)` |
| `deal_currency_code` | Currency | enumeration | `varchar` |
| `hs_is_closed` | Is closed | bool | `boolean` |
| `hs_is_closed_won` | Is Closed Won | bool | `boolean` |
| `days_to_close` | Days to close | number | `bigint` |
| `hs_closed_amount` | Closed amount | number | `numeric(18,2)` |
| `closed_lost_reason` | Closed lost reason | string | `varchar` |
| `closed_won_reason` | Closed won reason | string | `varchar` |
| `hs_next_step` | Next step | string | `varchar` |
| `hs_analytics_source` | Original Traffic Source | enumeration | `varchar` |
| `num_associated_contacts` | Number of associated contacts | number | `bigint` |
| `num_contacted_notes` | Number of times contacted | number | `bigint` |
| `notes_last_updated` | Last activity date | datetime | `timestamptz` |

### All HubSpot default deal properties
Grouped as in HubSpot's knowledge base article [*HubSpot's default deal properties*](https://knowledge.hubspot.com/properties/hubspots-default-deal-properties). The article lists labels only; internal names below are HubSpot's standard names. The **authoritative list for a real account**, including custom properties and exact types, comes from `GET /crm/v3/properties/deals`: run `python scripts/export_deal_properties.py` against the account. [`deal-properties.md`](deal-properties.md) was generated this way from the local HubSpot mock's property catalogue.

**Deal information**
| Label | Internal name | Notes |
|---|---|---|
| Deal name | `dealname` | |
| Deal description | `description` | |
| Deal owner | `hubspot_owner_id` | owner ID |
| Deal type | `dealtype` | New Business / Existing Business (editable options) |
| Close date | `closedate` | expected or actual close date |
| Create date | `createdate` | set automatically, editable |
| Created by user ID | `hs_created_by_user_id` | set automatically |
| Updated by user ID | `hs_updated_by_user_id` | set automatically |
| Record ID | `hs_object_id` | unique deal ID |
| Record Source / Detail 1-3 | `hs_object_source`, `hs_object_source_detail_1..3` | how the deal was created |
| Priority | `hs_priority` | low / medium / high |
| Deal probability | `hs_deal_stage_probability` | from the stage's win probability |
| Weighted amount | `hs_projected_amount` | amount × deal probability |
| Forecast amount | `hs_forecast_amount` | amount × forecast probability |
| Forecast category | `hs_manual_forecast_category` | Not forecasted / Pipeline / Best case / Commit / Closed won |
| Forecast probability | `hs_forecast_probability` | |
| Next step | `hs_next_step` | |
| Deal score | `hs_deal_score` | HubSpot AI health score |
| Deal collaborator | `hs_all_collaborator_owner_ids` | |
| Deal split added | `hs_deal_split_added` | |
| Deal tags | `hs_tag_ids` | |
| HubSpot team | `hubspot_team_id` | |
| Brands | `hs_all_assigned_business_unit_ids` | |
| Shared teams / users | `hs_shared_team_ids`, `hs_shared_user_ids` | |
| Merged Deal IDs | `hs_merged_object_ids` | |
| Next Meeting ID / Name / Start Time | `hs_next_meeting_id`, `hs_next_meeting_name`, `hs_next_meeting_start_time` | |
| Number of associated contacts | `num_associated_contacts` | |

**Deal activity**
| Label | Internal name |
|---|---|
| Deal stage | `dealstage` |
| Pipeline | `pipeline` |
| Is Closed Won / Is closed lost | `hs_is_closed_won`, `hs_is_closed_lost` |
| Closed won reason / Closed lost reason | `closed_won_reason`, `closed_lost_reason` |
| Last activity date | `notes_last_updated` |
| Last contacted | `notes_last_contacted` |
| Next activity date | `notes_next_activity_date` |
| Last modified date | `hs_lastmodifieddate` |
| Number of Sales Activities | `num_notes` |
| Number of times contacted | `num_contacted_notes` |
| Owner assigned date | `hubspot_owner_assigneddate` |
| Latest Approval Status | `hs_latest_approval_status` |
| Date of last meeting booked in meetings tool | `engagements_last_meeting_booked` |
| Campaign / Medium / Source of last booking in meetings tool | `engagements_last_meeting_booked_campaign`, `..._medium`, `..._source` |

**Deal revenue**
| Label | Internal name |
|---|---|
| Amount | `amount` |
| Amount in company currency | `amount_in_home_currency` |
| Currency | `deal_currency_code` |
| Exchange rate | `hs_exchange_rate` |
| Annual contract value (ACV) | `hs_acv` |
| Annual recurring revenue (ARR) | `hs_arr` |
| Monthly recurring revenue (MRR) | `hs_mrr` |
| Total contract value (TCV) | `hs_tcv` |

**Analytics history**
| Label | Internal name |
|---|---|
| Original Traffic Source / drill-down 1 / drill-down 2 | `hs_analytics_source`, `hs_analytics_source_data_1`, `hs_analytics_source_data_2` |
| Latest Traffic Source / drill-down 1 / drill-down 2 / date | `hs_analytics_latest_source`, `..._data_1`, `..._data_2`, `hs_analytics_latest_source_timestamp` |

**Calculated, stage and recurring revenue properties**
| Label | Internal name | Availability |
|---|---|---|
| Average Deal Owner Duration In Current Stage | `hs_average_deal_owner_duration_in_current_stage` | all tiers |
| Is Stalled After Timestamp | `hs_is_stalled_after_timestamp` | all tiers |
| Date entered / exited `<stage>` | `hs_v2_date_entered_<stageId>`, `hs_v2_date_exited_<stageId>` | Professional / Enterprise |
| Latest / Cumulative time in `<stage>` | `hs_v2_latest_time_in_<stageId>`, `hs_v2_cumulative_time_in_<stageId>` | Professional / Enterprise |
| Date entered current stage / Time in current stage | `hs_v2_date_entered_current_stage`, `hs_v2_time_in_current_stage` | Professional / Enterprise |
| Recurring revenue amount / deal type / inactive date / inactive reason | `recurring_revenue_amount`, `recurring_revenue_deal_type`, `recurring_revenue_inactive_date`, `recurring_revenue_inactive_reason` | Enterprise |
| Closed amount / Days to close | `hs_closed_amount`, `days_to_close` | used in custom reports |

---

## 🔒 Security Requirements

### **Token Permissions**
#### ✅ **Required (Minimum Permissions)**
- `crm.objects.deals.read`

#### 🔧 **Optional (Test data setup only)**
- `crm.objects.deals.write` - only for `scripts/create_test_deals.py`; remove it from tokens used in production.

### **Handling rules**
- Never commit a token. `.env` is git-ignored; `.env.example` holds a placeholder.
- Rotate the token in HubSpot if it is ever exposed; the service will fail the next scan with a clear 401.
- Change `CONFIG_PASSWORD` (token encryption key) and `SECRET_KEY` outside development.

---

## 📈 Monitoring & Debugging

### **Request headers sent**
```http
Authorization: Bearer pat-***
Accept: application/json
User-Agent: HubSpot-Deals-ETL/1.0
```

### **What is logged per HubSpot call** (JSON logs, `logs/app.log` and stdout)
`operation` (e.g. `hubspot_get_deals`), HTTP method, `status_code`, `duration_ms`, `attempt`, and on failure `hubspot_message` and `correlation_id`. Retries log `retry_in_seconds` and the 429 `policy`.

### **API usage metrics**
The latest `X-HubSpot-RateLimit-*` values are attached to each checkpoint (`checkpoint_data.rate_limit`) and to the completed scan (`metadata.rate_limit`), visible via `GET /api/v1/scan/{scanId}/status`.

---

## 🧪 Testing the API Integration

### **Test authentication directly against HubSpot**
```bash
curl -s "https://api.hubapi.com/crm/v3/objects/deals?limit=1" \
  -H "Authorization: Bearer $HUBSPOT_ACCESS_TOKEN"
```

### **Test authentication through the service**
The service API itself requires an HMAC-signed request (see [api-documentation.md](api-documentation.md#-authentication)):
```bash
python scripts/signed_request.py POST /auth/validate "{\"accessToken\": \"$HUBSPOT_ACCESS_TOKEN\"}"
```

### **List deal properties / pipelines**
```bash
curl -s "https://api.hubapi.com/crm/v3/properties/deals" -H "Authorization: Bearer $HUBSPOT_ACCESS_TOKEN"
curl -s "https://api.hubapi.com/crm/v3/pipelines/deals"  -H "Authorization: Bearer $HUBSPOT_ACCESS_TOKEN"
```

### **Automated tests**
- `pytest` - unit tests with a mocked HTTP session: auth header, query params, cursor pagination, 401/403, 429 retry (burst and daily), 5xx/network retry, rate limiter, transformations, batching/resume.
- `tests/test_mock_hubspot_contract.py` - contract tests that keep the HubSpot mock faithful to the behaviour documented here.
- `scripts/run_extraction_test.py` - end-to-end run against a real HubSpot test account; writes evidence to `test-results/`.

---

## 🚨 Common Issues & Solutions

| Issue | Cause | Solution |
|---|---|---|
| `401 ... Authentication credentials not found` | Token missing, mistyped, or revoked | Copy the token again from the private app; check for whitespace |
| `403` mentioning required scopes | Private app lacks the scope | Edit the private app → Scopes → add `crm.objects.deals.read`, then use the new token |
| `403` when creating test deals | Missing `crm.objects.deals.write` | Add the write scope (test accounts only) |
| `429 ... ten_secondly_rolling` repeatedly | Other apps share the quota, or limit set above the account tier | Lower `HUBSPOT_RATE_LIMIT_MAX_REQUESTS` (100 for Free/Starter) |
| `429 ... daily limit` | Daily quota exhausted | Wait for the reset at midnight (account time zone) |
| Scan completes with 0 deals | Account has no active deals, or deals are archived | Check in HubSpot; use `filters.archived: true` for deleted deals |
| `dealstage` shows numbers | Custom stages have numeric IDs | Map with `GET /crm/v3/pipelines/deals` |

---

## 🧪 Local HubSpot API Mock

HubSpot developer account creation for this assignment required government-ID verification. With the project manager's approval, development and all recorded tests use a local mock that matches the HubSpot REST endpoints described in this document: [`mock_hubspot/`](../mock_hubspot/README.md).

| Aspect | Matched behaviour |
|---|---|
| Endpoints | `GET/POST /crm/v3/objects/deals`, `GET/PATCH/DELETE /crm/v3/objects/deals/{id}`, `GET /crm/v3/properties/deals`, `GET /crm/v3/pipelines/deals`, `GET /account-info/v3/details` |
| Auth | `Authorization: Bearer <private app token>`; unknown token → 401 with HubSpot's `INVALID_AUTHENTICATION` body |
| Scopes | `crm.objects.deals.read` for reads, `crm.objects.deals.write` for writes; missing → 403 `MISSING_SCOPES` |
| Pagination | `limit` (default 10, max 100), `after` cursor, `paging.next.{after,link}` absent on the last page |
| Properties | string values, `null` for empty requested properties, unknown names omitted, HubSpot's default set when none requested, calculated properties maintained |
| Errors | HubSpot error body `{status, message, correlationId, category}`; validation errors per property |
| Rate limits | `X-HubSpot-RateLimit-*` headers on every response; 429 with `policyName` `TEN_SECONDLY_ROLLING` or `DAILY` |

Switching between the mock and HubSpot needs no code changes:

| Setting | Mock | HubSpot |
|---|---|---|
| `HUBSPOT_API_BASE_URL` (service) | `http://hubspot_mock:5299` (set by `docker-compose.mock.yml`) | `https://api.hubapi.com` |
| Access token in scan requests | `pat-mock-test-account-deals` | private app token `pat-...` |

The mock also offers admin endpoints (`/__mock/faults`, `/__mock/config`) to inject 429/5xx responses, latency and stricter quotas. `scripts/run_mock_resilience_test.py` uses them to test the retry, back-off and resume behaviour described above.

### Verification status of the HubSpot facts in this document
- **Checked against HubSpot during this project:**
  - endpoint paths and query parameters (`properties`, `propertiesWithHistory`, `associations`) and scopes ([Deals API guide](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/deals/guide))
  - rate-limit tiers, headers and 429 policies ([usage guidelines](https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines))
  - default property labels ([knowledge base](https://knowledge.hubspot.com/properties/hubspots-default-deal-properties))
  - the 401 message (live API)
- **Based on HubSpot's documented conventions and general API knowledge, not yet confirmed against a live account:**
  - `limit` default 10 and maximum 100
  - the default property set returned
  - default pipeline stage IDs
  - internal names of non-core properties
  - token lifetime
  - search API limits

---

## 📞 Support Resources
- HubSpot API usage guidelines and limits: https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
- Deals API guide (CRM v3): https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/deals/guide
- Private apps: https://developers.hubspot.com/docs/apps/legacy-apps/private-apps/overview
- Default deal properties: https://knowledge.hubspot.com/properties/hubspots-default-deal-properties
