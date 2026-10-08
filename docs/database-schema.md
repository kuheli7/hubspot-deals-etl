# 🗄️ HubSpot Deals ETL - Database Schema

PostgreSQL schema design for the HubSpot deals extraction service: the job-tracking tables, the per-tenant `deals` table, how HubSpot property types map to PostgreSQL types, indexes, and multi-tenant isolation.

> The DDL below was taken from the running development database with `pg_dump --schema-only` after an extraction, so it is exactly what the service creates.

---

## 📋 Overview

| Area | Schema | Tables | Created by |
|---|---|---|---|
| Job tracking | `public` | `jobs`, `job_checkpoints` | SQLAlchemy models (`models/models.py`) at service start-up |
| Extracted deals | `hubspot_deals_<tenant>` (one per tenant) | `deals` | dlt, using the column hints in `services/data_source.py` |
| dlt bookkeeping | `hubspot_deals_<tenant>` | `_dlt_loads`, `_dlt_version`, `_dlt_pipeline_state` | dlt |
| Merge staging | `hubspot_deals_<tenant>_staging` | `deals` (transient) | dlt (used by the `merge` write disposition) |

`<tenant>` is the scan's `organizationId`, lower-cased with `-` replaced by `_`. For example, `org-12345` becomes the schema `hubspot_deals_org_12345`.

```
public.jobs 1 ──── * public.job_checkpoints          (job_checkpoints.job_id → jobs.id)
     │
     │ jobs.id = deals._scan_id  (logical link: which scan last wrote the row)
     ▼
hubspot_deals_<tenant>.deals   (one schema per jobs.organizationId)
```

---

## 🏗️ Table Schemas

### 1. `public.jobs` - one row per scan

```sql
CREATE TABLE public.jobs (
    id                 varchar(255) PRIMARY KEY,          -- scanId from the API request
    "organizationId"   varchar(255) NOT NULL,             -- tenant
    type               varchar(50)  NOT NULL,             -- 'deal'
    status             varchar(50)  NOT NULL,             -- pending | running | paused | resuming |
                                                          -- completed | failed | cancelled | crashed
    "startTime"        timestamptz  NOT NULL DEFAULT now(),
    "endTime"          timestamptz,
    "lastHeartbeat"    timestamptz,                       -- updated after every committed batch
    "recordsExtracted" integer,
    "errorMessage"     text,                              -- original HubSpot error on failure
    config             json,                              -- {"auth": <Fernet-encrypted>, "filters": {...}, "type": ["deal"]}
    job_metadata       json                               -- dataset_name, extraction_summary, indexes, rate_limit, ...
);
CREATE INDEX "ix_jobs_organizationId" ON public.jobs ("organizationId");
```

### 2. `public.job_checkpoints` - committed progress of a scan

```sql
CREATE TABLE public.job_checkpoints (
    id                      serial PRIMARY KEY,
    job_id                  varchar(255) NOT NULL REFERENCES public.jobs(id),
    "createdAt"             timestamptz  NOT NULL DEFAULT now(),
    phase                   varchar(50)  NOT NULL,   -- deals_batch_committed | deals_paused |
                                                     -- deals_cancelled | deals_completed
    "recordsProcessed"      integer,                 -- deals loaded so far (cumulative)
    "totalEstimated"        integer,
    cursor                  varchar(500),            -- HubSpot paging.next.after for the next page
    "pageNumber"            integer,                 -- pages loaded so far
    "batchSize"             integer,                 -- page size used
    "lastProcessedId"       varchar(255),
    "lastProcessedTimestamp" timestamptz,
    checkpoint_data         json                     -- batches_completed, stop_reason, load_ids, rate_limit, ...
);
CREATE INDEX ix_job_checkpoints_job_id ON public.job_checkpoints (job_id);
```

A checkpoint is written **only after a batch of pages has been loaded** into `deals`, so `cursor` always marks the first page that is *not* yet in the database. Resume reads the latest checkpoint for the job.

### 3. `hubspot_deals_<tenant>.deals` - extracted deals

```sql
CREATE SCHEMA hubspot_deals_org_12345;

CREATE TABLE hubspot_deals_org_12345.deals (
    -- HubSpot identity
    id                        varchar      NOT NULL,   -- HubSpot deal ID (merge key)
    -- Deal properties (HubSpot "properties" object, typed)
    dealname                  varchar,
    amount                    numeric(18,2),
    dealstage                 varchar,                 -- stage internal ID, e.g. 'closedwon'
    pipeline                  varchar,                 -- pipeline internal ID, e.g. 'default'
    dealtype                  varchar,                 -- 'newbusiness' | 'existingbusiness'
    closedate                 timestamptz,
    createdate                timestamptz,
    hs_lastmodifieddate       timestamptz,
    description               varchar,
    hubspot_owner_id          varchar,
    hs_object_id              bigint,
    hs_priority               varchar,                 -- 'low' | 'medium' | 'high'
    hs_deal_stage_probability double precision,        -- 0.0 - 1.0
    hs_forecast_amount        numeric(18,2),
    hs_projected_amount       numeric(18,2),           -- weighted amount
    amount_in_home_currency   numeric(18,2),
    deal_currency_code        varchar,
    hs_is_closed              boolean,
    hs_is_closed_won          boolean,
    days_to_close             bigint,
    hs_closed_amount          numeric(18,2),
    closed_lost_reason        varchar,
    closed_won_reason         varchar,
    hs_next_step              varchar,
    hs_analytics_source       varchar,
    num_associated_contacts   bigint,
    num_contacted_notes       bigint,
    notes_last_updated        timestamptz,
    -- HubSpot object-level fields
    archived                  boolean,
    created_at                timestamptz,             -- object createdAt
    updated_at                timestamptz,             -- object updatedAt
    archived_at               timestamptz,
    -- ETL metadata
    _extracted_at             timestamptz  NOT NULL,   -- when the page was fetched
    _scan_id                  varchar      NOT NULL,   -- jobs.id of the scan that last wrote the row
    _tenant_id                varchar      NOT NULL,   -- jobs."organizationId"
    _page_number              bigint,                  -- HubSpot page the row came from
    _source_service           varchar,                 -- 'hubspot_deals'
    -- dlt lineage
    _dlt_load_id              varchar      NOT NULL,   -- → _dlt_loads.load_id
    _dlt_id                   varchar      NOT NULL,   -- dlt row hash
    CONSTRAINT deals_id_key      UNIQUE (id),
    CONSTRAINT deals__dlt_id_key UNIQUE (_dlt_id)
);
```

dlt creates the table from the column hints in `DEAL_COLUMN_HINTS`, so columns exist (with the right type) even when every value is `NULL` on the first load. Extra properties requested with `filters.properties` are added as new `varchar` columns automatically (dlt schema evolution).

---

## 🔄 HubSpot → PostgreSQL Type Mapping

HubSpot returns **every property value as a string** (or `null`). `transform_deal()` converts values before loading; the dlt column hint fixes the PostgreSQL type.

| HubSpot `type` | Example raw value | Python conversion | dlt type | PostgreSQL type | Columns |
|---|---|---|---|---|---|
| `string` | `"Acme Corp - Starter Plan"` | `str`, blank → `None` | `text` | `varchar` | dealname, description, closed_*_reason, hs_next_step |
| `enumeration` | `"closedwon"` | `str` (internal value) | `text` | `varchar` | dealstage, pipeline, dealtype, hs_priority, hubspot_owner_id, deal_currency_code, hs_analytics_source |
| `number` (currency) | `"25000.50"` | `Decimal` | `decimal(18,2)` | `numeric(18,2)` | amount, hs_forecast_amount, hs_projected_amount, amount_in_home_currency, hs_closed_amount |
| `number` (count / ID) | `"18374659201"` | `int` | `bigint` | `bigint` | hs_object_id, days_to_close, num_associated_contacts, num_contacted_notes |
| `number` (ratio) | `"0.6"` | `float` | `double` | `double precision` | hs_deal_stage_probability |
| `datetime` | `"2026-11-30T17:00:00.000Z"` | aware UTC `datetime` | `timestamp` | `timestamptz` | closedate, createdate, hs_lastmodifieddate, notes_last_updated |
| `date` | `"2026-11-30"` or epoch ms `"1796058000000"` | UTC midnight / from epoch ms | `timestamp` | `timestamptz` | (any date property requested) |
| `bool` | `"true"` | `bool` | `bool` | `boolean` | hs_is_closed, hs_is_closed_won |
| object field `archived` | `false` | `bool` | `bool` | `boolean` | archived |
| any other requested property | `"EMEA"` | `str` | inferred `text` | `varchar` | custom properties |

**Design choices**
- **`numeric(18,2)` for money**: exact decimal arithmetic for sums and reports (no floating-point drift); 16 integer digits cover any realistic deal size.
- **`timestamptz` for dates**: HubSpot values are UTC; storing them with time zone avoids ambiguity when clients query from other zones.
- **`varchar` (unbounded) for text and enumerations**: HubSpot labels and custom option values change; storing internal IDs as text avoids migrations when a pipeline is edited. Labels can be joined from `GET /crm/v3/pipelines/deals`.
- **`id` as `varchar`**: HubSpot IDs are numeric strings that exceed 32-bit range; keeping them as text matches the API and the `after` cursor. `hs_object_id` provides the numeric form.
- **Unparseable values become `NULL`** rather than failing the whole load.

---

## 🏷️ ETL Metadata Fields

| Column | Type | Source | Purpose |
|---|---|---|---|
| `_extracted_at` | `timestamptz NOT NULL` | time the HubSpot page was fetched | freshness, ordering of results |
| `_scan_id` | `varchar NOT NULL` | `jobs.id` | which scan wrote the row; results API and `DELETE /scan/{id}/remove` filter on it |
| `_tenant_id` | `varchar NOT NULL` | `jobs."organizationId"` | tenant isolation and tenant-scoped queries |
| `_page_number` | `bigint` | HubSpot page counter | debugging and checkpoint verification |
| `_source_service` | `varchar` | constant `hubspot_deals` | lineage when tables are combined downstream |
| `_dlt_load_id` | `varchar NOT NULL` | dlt | joins to `_dlt_loads` (load time, status) |
| `_dlt_id` | `varchar NOT NULL` | dlt | unique row hash |

---

## 📈 Indexes

| Index | Columns | Serves |
|---|---|---|
| `deals_id_key` (unique) | `id` | merge upserts, lookups by HubSpot ID |
| `idx_deals_tenant` | `_tenant_id` | tenant filters / row-level security |
| `idx_deals_tenant_stage` | `_tenant_id, pipeline, dealstage` | pipeline reports: deals and value per stage |
| `idx_deals_tenant_closedate` | `_tenant_id, closedate` | forecasts and "closing this quarter" queries |
| `idx_deals_createdate` | `createdate` | new deals over time |
| `idx_deals_lastmodified` | `hs_lastmodifieddate` | incremental / change queries |
| `idx_deals_scan` | `_scan_id` | results API pagination and scan removal |

```sql
CREATE INDEX IF NOT EXISTS idx_deals_tenant           ON hubspot_deals_org_12345.deals (_tenant_id);
CREATE INDEX IF NOT EXISTS idx_deals_tenant_stage     ON hubspot_deals_org_12345.deals (_tenant_id, pipeline, dealstage);
CREATE INDEX IF NOT EXISTS idx_deals_tenant_closedate ON hubspot_deals_org_12345.deals (_tenant_id, closedate);
CREATE INDEX IF NOT EXISTS idx_deals_createdate       ON hubspot_deals_org_12345.deals (createdate);
CREATE INDEX IF NOT EXISTS idx_deals_lastmodified     ON hubspot_deals_org_12345.deals (hs_lastmodifieddate);
CREATE INDEX IF NOT EXISTS idx_deals_scan             ON hubspot_deals_org_12345.deals (_scan_id);
```

dlt creates the unique constraints. The secondary indexes are created by `DatabaseService.ensure_deal_indexes()` when a scan completes. `IF NOT EXISTS` makes this idempotent across scans.

---

## 🛡️ Multi-Tenant Data Isolation

Isolation works at four layers:

1. **Schema per tenant.** Every tenant's deals live in `hubspot_deals_<tenant>`. Queries, scan removal and `DROP SCHEMA` for off-boarding never touch another tenant's data. The dlt pipeline state and staging tables are per schema too.
2. **Tenant column on every row.** `_tenant_id` (NOT NULL) is redundant inside a tenant schema on purpose. It survives exports and cross-tenant reporting views, and can drive row-level security.
3. **Validated tenant identifiers.** `organizationId` must match `^[a-zA-Z0-9_-]{1,40}$`, so it is safe to use in a schema name (≤ 63-character PostgreSQL identifiers) and cannot inject SQL. Values bound into queries (`_scan_id` filters) use parameters, not string formatting.
4. **Credentials per scan.** Each scan carries its own HubSpot token (encrypted at rest), so a tenant can only extract the HubSpot account its own token grants.

**Optional hardening for shared analytics access**
```sql
-- One read-only role per tenant, limited to its own schema
CREATE ROLE tenant_org_12345_reader NOLOGIN;
GRANT USAGE  ON SCHEMA hubspot_deals_org_12345 TO tenant_org_12345_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA hubspot_deals_org_12345 TO tenant_org_12345_reader;

-- Or, for a combined reporting view across tenants, enforce row-level security on _tenant_id
ALTER TABLE hubspot_deals_org_12345.deals ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON hubspot_deals_org_12345.deals
    USING (_tenant_id = current_setting('app.tenant_id'));
```

**Tenant off-boarding**
```sql
DROP SCHEMA hubspot_deals_org_12345 CASCADE;
DROP SCHEMA IF EXISTS hubspot_deals_org_12345_staging CASCADE;
DELETE FROM public.job_checkpoints WHERE job_id IN (SELECT id FROM public.jobs WHERE "organizationId" = 'org-12345');
DELETE FROM public.jobs WHERE "organizationId" = 'org-12345';
```

---

## 🔁 Load Semantics

| Aspect | Behaviour |
|---|---|
| Write disposition | `merge` on primary key `id` (dlt delete-insert through the `_staging` schema) |
| Re-running a scan | upserts: each deal keeps one row, refreshed with the latest values and `_scan_id` |
| Pause / crash / resume | each batch of N pages is loaded, then checkpointed; resume continues from the stored cursor and can re-read at most one partially processed batch, which `merge` deduplicates |
| Removing a scan | `DELETE FROM deals WHERE _scan_id = %s`, then the job and its checkpoints are deleted |
| Cleanup | `POST /api/v1/maintenance/cleanup {"daysOld": 7}` removes finished job records (and their checkpoints) older than N days; extracted deal rows are kept |

---

## 📊 Common Queries

```sql
-- Pipeline summary for a tenant
SELECT pipeline, dealstage, count(*) AS deals, sum(amount) AS total_amount
FROM hubspot_deals_org_12345.deals
WHERE _tenant_id = 'org-12345' AND NOT archived
GROUP BY pipeline, dealstage
ORDER BY pipeline, total_amount DESC;

-- Weighted forecast for deals closing this quarter
SELECT sum(amount * coalesce(hs_deal_stage_probability, 0)) AS weighted_forecast
FROM hubspot_deals_org_12345.deals
WHERE _tenant_id = 'org-12345'
  AND closedate >= date_trunc('quarter', now())
  AND closedate <  date_trunc('quarter', now()) + interval '3 months'
  AND NOT coalesce(hs_is_closed, false);

-- Win rate on closed deals
SELECT round(100.0 * count(*) FILTER (WHERE hs_is_closed_won) / nullif(count(*), 0), 1) AS win_rate_pct
FROM hubspot_deals_org_12345.deals
WHERE hs_is_closed;

-- Deals written by a given scan (what the results API returns)
SELECT id, dealname, amount, dealstage, closedate
FROM hubspot_deals_org_12345.deals
WHERE _scan_id = 'deals-e2e-001'
ORDER BY _extracted_at DESC, id
LIMIT 100 OFFSET 0;

-- Scan progress and checkpoints
SELECT j.id, j.status, j."recordsExtracted", c.phase, c."pageNumber", c."recordsProcessed", c.cursor, c."createdAt"
FROM public.jobs j
LEFT JOIN public.job_checkpoints c ON c.job_id = j.id
WHERE j.id = 'deals-e2e-001'
ORDER BY c.id;

-- Load history
SELECT load_id, status, inserted_at FROM hubspot_deals_org_12345._dlt_loads ORDER BY inserted_at DESC;
```

---

## 📈 Performance & Retention

- **Batch size**: 100 deals per HubSpot page; one `pipeline.run` per 10 pages (1,000 deals) by default. Both are configurable (`HUBSPOT_PAGE_SIZE`, `HUBSPOT_CHECKPOINT_INTERVAL_PAGES`, or per scan `pageSize` / `checkpointInterval`).
- **Results API pagination** uses `LIMIT/OFFSET` over `(_extracted_at DESC, id)` filtered by `_scan_id`, which `idx_deals_scan` serves.
- **Large accounts**: a safety stop at 10,000 pages (1,000,000 deals) per scan prevents runaway jobs; the job metadata flags `truncated_at_page_limit` if it is reached.
- **Retention**: finished job records older than N days (default `CLEANUP_DAYS`) are removed through the maintenance endpoint; deal rows persist until their scan is removed or the tenant schema is dropped.
