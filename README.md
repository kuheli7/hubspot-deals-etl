# HubSpot Deals ETL

A Flask-RESTX service that extracts **deals** from the HubSpot CRM API (v3) and loads them into PostgreSQL with [dlt](https://dlthub.com/). Scans run asynchronously, commit checkpoints every N pages, and can be paused, resumed, cancelled and recovered after a crash.

The service structure was generated with the [Glynac-AI DLT Generator](https://github.com/Glynac-AI/Backend-Tools-and-assessment) (`hubspot-deals-config.json`) and then implemented for HubSpot deals.

| | |
|---|---|
| Source | HubSpot CRM v3 `GET /crm/v3/objects/deals` (private app token, cursor pagination) |
| Destination | PostgreSQL 15, one schema per tenant (`hubspot_deals_<tenant>.deals`) |
| API | `http://localhost:5200/api/v1`, Swagger UI at `http://localhost:5200/docs/` |
| Ports | dev **5200**, stage **5201**, prod **5202** |

## Features
- **HubSpot API client** (`services/hubspot_api_service.py`): Bearer auth, cursor pagination, 150 requests / 10 s sliding-window rate limiter, `X-HubSpot-RateLimit-*` header guard, 429/5xx/network retries with back-off, typed errors, credential validation.
- **DLT data source** (`services/data_source.py`): 28 deal properties converted to typed columns (`numeric(18,2)`, `timestamptz`, `boolean`, `bigint`), ETL metadata (`_extracted_at`, `_scan_id`, `_tenant_id`), `merge` on `id`.
- **Checkpoints that never skip data**: each batch of N pages is loaded *before* its checkpoint is committed; pause/resume and crash recovery continue from the stored cursor.
- **Multi-tenant isolation**: schema per tenant, `_tenant_id` on every row, validated tenant IDs, indexes on tenant/stage/dates.
- **Security**: tokens arrive per request, are Fernet-encrypted at rest and redacted from every response and log.
- **HubSpot API mock** (`mock_hubspot/`): CRM v3 deals endpoints with HubSpot's auth, scopes, pagination, errors and rate-limit headers, plus fault injection for resilience tests.

> **HubSpot access for this submission.** Creating the HubSpot developer account required government-ID verification. With the project manager's approval, the service was therefore tested against a **local mock of HubSpot's CRM v3 REST API** ([`mock_hubspot/`](mock_hubspot/README.md)). It reproduces HubSpot's deals endpoints, auth, scopes, pagination, response and error bodies, and rate-limit headers. The ETL code is identical for real HubSpot: only the base URL and token change. Details: [`test-results/hubspot-account-setup.md`](test-results/hubspot-account-setup.md).

## Quick Start

### Prerequisites
- Docker Desktop (Compose v2)
- Python 3.11 for the tests and helper scripts
- For real HubSpot only: a private app access token with `crm.objects.deals.read` (see [HubSpot setup](#hubspot-setup))

### 1. Configure
```bash
git clone https://github.com/kuheli7/hubspot-deals-etl.git
cd hubspot-deals-etl
cp .env.example .env          # .env is git-ignored
python -m venv .venv && .venv/Scripts/activate      # Windows; use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt pytest
```
Choose a mode in `.env`:
- **Local HubSpot mock** (what the test results use): uncomment the lines in the "Local HubSpot API mock" section (`COMPOSE_FILE`, `HUBSPOT_API_BASE_URL=http://localhost:5299`, mock token).
- **Real HubSpot**: keep `HUBSPOT_API_BASE_URL=https://api.hubapi.com` and set `HUBSPOT_ACCESS_TOKEN=pat-...`.

### 2. Start the services
```bash
docker-compose up -d --build                     # uses the mock too when COMPOSE_FILE is set in .env
# or name the mock override explicitly:
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build

docker-compose ps             # all containers should be healthy
curl http://localhost:5200/health
```
```json
{"status": "healthy", "service": "hubspot_deals", "checks": {"database": "ok"}, "...": "..."}
```

### 3. Create the 5 test deals
```bash
python scripts/create_test_deals.py     # POST /crm/v3/objects/deals x5, IDs -> test-results/test_deals_created.json
```

### 4. Run an extraction
```bash
curl -X POST http://localhost:5200/api/v1/scan/start \
  -H "Content-Type: application/json" \
  -d '{"config": {"scanId": "deals-001", "organizationId": "org-12345", "type": ["deal"],
       "auth": {"accessToken": "pat-mock-test-account-deals"}}}'

curl http://localhost:5200/api/v1/scan/deals-001/status
curl "http://localhost:5200/api/v1/results/deals-001/result?tableName=deals&limit=100"
```
With real HubSpot, use your own `pat-...` token.

### 5. Inspect the database
```bash
docker-compose exec postgres_dev psql -U postgres -d hubspot_deals_data_dev \
  -c "SELECT id, dealname, amount, dealstage, closedate FROM hubspot_deals_org_12345.deals"
```

Open **http://localhost:5200/docs/** for the interactive API documentation.

## HubSpot setup
1. Create a free account at [developers.hubspot.com](https://developers.hubspot.com/) and create a **test account** from the developer portal.
2. In the test account: **Settings → Integrations → Private Apps → Create a private app** named `DLT Deals Extractor`.
3. Scopes: `crm.objects.deals.read` (add `crm.objects.deals.write` only while the script creates the test deals).
4. Create the app, copy the access token and put it in `.env` as `HUBSPOT_ACCESS_TOKEN`. Never commit it.

With the mock, the equivalent token `pat-mock-test-account-deals` already has both scopes. Other mock tokens cover read-only, no-scope and a 2,500-deal load-test account ([`mock_hubspot/README.md`](mock_hubspot/README.md#accounts-and-tokens)).

## Testing
```bash
pytest                                        # 33 unit + mock-contract tests, no network needed
python scripts/run_extraction_test.py --restart-test --crash-test [--mock-latency-ms 1000]
python scripts/run_mock_resilience_test.py    # mock only: volume, rate limits, outages, scopes, archived
python scripts/export_deal_properties.py      # deal property list -> docs/deal-properties.md
```
`run_extraction_test.py` covers:
- health and docs checks, and token validation
- extraction of all 5 test deals, verified field by field
- the PostgreSQL schema and indexes
- checkpointing: a scan with 1-deal pages is paused and resumed
- edge cases: invalid token, malformed JSON, injection, unknown IDs, duplicates, wrong-state cancels
- crash recovery (`--crash-test`): the container is restarted mid-scan
- survival across restarts (`--restart-test`)

`--mock-latency-ms` slows the mock down so the pause and the crash reliably land mid-scan.

`run_mock_resilience_test.py` uses the mock's admin endpoints to:
- extract 2,500 deals over 25 pages
- impose a 20 requests / 10 s quota
- inject 502/503 outages, then resume the failed scan from its checkpoint
- hit the daily limit
- use a token without the deals scope
- extract archived deals

Latest results: **33/33 tests, 40/40 end-to-end checks, 9/9 resilience checks**. See [`test-results/`](test-results/README.md).

## Configuration
All settings are environment variables (see [`.env.example`](.env.example)); `docker-compose.yml` reads the HubSpot ones from `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `HUBSPOT_API_BASE_URL` | `https://api.hubapi.com` | HubSpot API host |
| `HUBSPOT_API_TIMEOUT` | `30` | Request timeout (s) |
| `HUBSPOT_RATE_LIMIT_MAX_REQUESTS` / `_WINDOW_SECONDS` | `150` / `10` | Client-side burst limit (use 100 on Free/Starter) |
| `HUBSPOT_RETRY_ATTEMPTS` | `3` | Retries for 429 / 5xx / network errors |
| `HUBSPOT_PAGE_SIZE` | `100` | Deals per request (max 100) |
| `HUBSPOT_CHECKPOINT_INTERVAL_PAGES` | `10` | Pages per loaded + checkpointed batch |
| `HUBSPOT_PAGE_DELAY_SECONDS` | `0` | Testing aid: delay between pages to make pause timing deterministic |
| `CONFIG_PASSWORD` | dev value | Key for encrypting stored tokens - change outside development |
| `HUBSPOT_ACCESS_TOKEN` | - | Used only by `scripts/`; the service receives tokens per request |

Per-scan overrides: `filters.pageSize`, `filters.checkpointInterval`, `filters.properties`, `filters.archived`.

## API overview
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/scan/start` | Start a deal extraction (202) |
| `GET` | `/api/v1/scan/{scanId}/status` | Status, progress, latest checkpoint |
| `POST` | `/api/v1/scan/{scanId}/pause` | Pause at the next page boundary |
| `POST` | `/api/v1/scan/{scanId}/resume` | Resume a paused, crashed or failed scan from its checkpoint |
| `POST` | `/api/v1/scan/{scanId}/cancel` | Cancel |
| `DELETE` | `/api/v1/scan/{scanId}/remove` | Delete a scan and its rows |
| `GET` | `/api/v1/scan/list` | List scans (paginated) |
| `GET` | `/api/v1/scan/statistics` | Scan statistics |
| `GET` | `/api/v1/results/{scanId}/tables` | Tables of a completed scan |
| `GET` | `/api/v1/results/{scanId}/result` | Extracted deals (paginated) |
| `POST` | `/api/v1/auth/validate` | Check a HubSpot token |
| `GET` | `/health` · `/api/v1/health` | Health checks |
| `POST` | `/api/v1/maintenance/cleanup` · `/detect-crashed` | Maintenance |

Full reference: [`docs/api-documentation.md`](docs/api-documentation.md).

## Documentation
| Document | Contents |
|---|---|
| [`docs/api-integration.md`](docs/api-integration.md) | HubSpot CRM v3 deals endpoint, auth, query parameters, response structure, rate limits, error handling, deal properties |
| [`docs/database-schema.md`](docs/database-schema.md) | PostgreSQL tables (`CREATE TABLE`), type mapping, ETL metadata, indexes, multi-tenant isolation |
| [`docs/api-documentation.md`](docs/api-documentation.md) | Service REST API with request/response examples and status codes |
| [`docs/deal-properties.md`](docs/deal-properties.md) | Deal property catalogue served by `GET /crm/v3/properties/deals` (generated) |
| [`mock_hubspot/README.md`](mock_hubspot/README.md) | HubSpot API mock: endpoints, behaviour matched, tokens, admin endpoints |
| [`test-results/`](test-results/) | Evidence from the test runs |

## Project structure
```
hubspot-deals-etl/
├── app.py                         # Flask app factory, /health, endpoint index
├── config.py                      # Environment-based config incl. HubSpot settings
├── api/
│   ├── routes.py                  # REST endpoints (scan, results, auth, maintenance)
│   ├── schemas.py                 # Marshmallow request validation
│   └── swagger_schemas.py         # OpenAPI models for /docs/
├── services/
│   ├── hubspot_api_service.py     # HubSpot client: auth, pagination, rate limits, errors
│   ├── data_source.py             # dlt resource, transformation, batching for checkpoints
│   ├── extraction_service.py      # Scan orchestration: batches, checkpoints, pause/resume
│   ├── job_service.py             # Job + checkpoint persistence
│   └── database_service.py        # Results queries, indexes, removal
├── models/                        # SQLAlchemy models (jobs, job_checkpoints)
├── mock_hubspot/                  # local HubSpot CRM v3 API mock (Flask, own Dockerfile)
├── scripts/                       # create_test_deals, run_extraction_test, run_mock_resilience_test, export_deal_properties
├── tests/                         # pytest unit tests + mock contract tests
├── docs/                          # Integration, schema and API documentation
├── test-results/                  # Test evidence (no secrets)
├── docker-compose.yml             # postgres, redis, service for dev/stage/prod profiles
├── docker-compose.mock.yml        # adds the HubSpot mock and points the dev service at it
├── Dockerfile.dev|stage|prod
├── hubspot-deals-config.json      # DLT Generator config used to create this project
└── .env.example
```

## Other environments
```bash
docker-compose --profile stage up -d postgres_stage hubspot_deals_service_stage   # port 5201
docker-compose --profile prod  up -d postgres_prod  hubspot_deals_service_prod    # port 5202 (gunicorn)
```
Production requires `SECRET_KEY` (≥ 32 chars) and `DB_PASSWORD`; set a strong `CONFIG_PASSWORD`.

## Changes to the generated template
Besides the HubSpot implementation, these generator-template issues were fixed:
- `cryptography==41.0.8` (does not exist on PyPI) → `44.0.3`; `dlt` pinned to `1.31.0`.
- Compose project name contained spaces/capitals (invalid) → `hubspot-deals-etl`.
- API prefix aligned to `/api/v1`; health checks pointed at a non-existent `/api/health` → `/health`; Swagger served at `/docs/`.
- `complete_job` was called twice; paused scans were marked completed; `replace` write disposition wiped earlier pages on resume (now `merge`); checkpoints could point past data that was not yet loaded.
- `cancel` overwrote job metadata; cleanup failed on jobs with checkpoints (foreign key); `check_database_health()` result was never evaluated.
- Plaintext token logging in the data source removed; stored (encrypted) auth redacted from API responses.
- Malformed JSON returned 500 instead of 400; pagination `max` limits were not enforced.
