# Local HubSpot API mock (CRM v3 deals)

A Flask service that reproduces the HubSpot CRM v3 REST endpoints, parameters, response bodies, error bodies and rate-limit headers that the deals ETL uses. The ETL code does not know it is talking to a mock: only `HUBSPOT_API_BASE_URL` and the token change.

**Why it exists.** Creating the HubSpot developer account for this assignment required government-ID verification (through Persona). The project manager approved testing against a mock *"as long as it matches the REST endpoints of HubSpot"*. All test evidence in `test-results/` was produced against this mock. The service runs unchanged against `https://api.hubapi.com` with a real private app token.

## Run it
```bash
# with the rest of the stack (recommended)
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build

# standalone
python -m mock_hubspot.server          # http://localhost:5299
```

Inside the compose network the service reaches the mock at `http://hubspot_mock:5299`; from the host it is `http://localhost:5299`.

## Accounts and tokens
Each token acts as a HubSpot **private app access token**, scoped to one mock account:

| Token (env override) | Account (portal ID) | Scopes | Used for |
|---|---|---|---|
| `pat-mock-test-account-deals` (`MOCK_HUBSPOT_TOKEN`) | Deals ETL Test Account (48600123), starts empty | `crm.objects.deals.read`, `crm.objects.deals.write` | the 5 test deals (`scripts/create_test_deals.py`) and all extraction tests |
| `pat-mock-test-account-readonly` (`MOCK_HUBSPOT_READONLY_TOKEN`) | Test Account | `crm.objects.deals.read` | read-only tests (create → 403) |
| `pat-mock-test-account-noscope` (`MOCK_HUBSPOT_NO_SCOPE_TOKEN`) | Test Account | none | missing-scope tests (read → 403) |
| `pat-mock-load-account-deals` (`MOCK_HUBSPOT_LOAD_TOKEN`) | Deals ETL Load Test Account (48600456), 2,500 generated deals (`MOCK_HUBSPOT_LOAD_DEALS`) | read, write | pagination and rate-limit tests |

Any other token gets HubSpot's 401. Data is persisted in `/data/hubspot_mock_state.json` (Docker volume `hubspot_deals_hubspot_mock_data`), so deals survive restarts like a real account.

## HubSpot endpoints implemented
| Endpoint | Scope | Behaviour reproduced |
|---|---|---|
| `GET /crm/v3/objects/deals` | read | `limit` (default 10, capped at 100; 50 with `propertiesWithHistory`), opaque `after` cursor, `paging.next.{after,link}` only when more results exist, `properties` (comma-separated or repeated), `propertiesWithHistory`, `archived=true` returns only archived deals, `associations` accepted |
| `POST /crm/v3/objects/deals` | write | `{"properties": {...}}` → `201`; validates names (`PROPERTY_DOESNT_EXIST`), read-only properties (`READ_ONLY_VALUE`), enumeration options and pipeline stages (`INVALID_OPTION`), numbers and dates |
| `GET /crm/v3/objects/deals/{id}` | read | `404 OBJECT_NOT_FOUND` for unknown or archived deals (unless `archived=true`) |
| `PATCH /crm/v3/objects/deals/{id}` | write | partial update, history kept for `propertiesWithHistory` |
| `DELETE /crm/v3/objects/deals/{id}` | write | archives (recycle bin) → `204`; sets `archived` / `archivedAt` |
| `GET /crm/v3/properties/deals[/{name}]` | read | property definitions with `name`, `label`, `type`, `fieldType`, `groupName`, `options`, `calculated`, `hubspotDefined`, ... (54 default deal properties) |
| `GET /crm/v3/pipelines/deals[/{id}]` | read | default "Sales Pipeline" with HubSpot's default stage IDs, `metadata.probability` and `metadata.isClosed` |
| `GET /account-info/v3/details` | - | `portalId`, `accountType: DEVELOPER_TEST`, currency, time zone |
| `GET /account-info/v3/api-usage/daily/private-apps` | - | daily usage vs. limit |

### Response conventions matched
- **Object shape:** `{"id", "properties", "createdAt", "updatedAt", "archived"[, "archivedAt"]}`.
- **Property values are strings or `null`.** Requested but empty properties come back as `null`; unknown requested names are omitted.
- **Default properties:** without `properties`, the list returns `amount, closedate, createdate, dealname, dealstage, hs_lastmodifieddate, hs_object_id, pipeline`. `createdate`, `hs_lastmodifieddate` and `hs_object_id` are always included.
- **HubSpot-calculated properties** are maintained on every write: `hs_object_id`, `hs_lastmodifieddate`, `hs_deal_stage_probability`, `hs_is_closed`, `hs_is_closed_won`, `hs_is_closed_lost`, `hs_projected_amount`, `amount_in_home_currency`, `hs_closed_amount`, `days_to_close`.
- **Results are ordered by ID**, and `after` is the ID where the next page starts.

### Errors and limits matched
| Situation | Status | Body |
|---|---|---|
| Missing or unknown token | 401 | `{"status":"error","message":"Authentication credentials not found. This API supports OAuth 2.0 authentication and you can find more details at https://developers.hubspot.com/docs/methods/auth/oauth-overview","correlationId":"…","category":"INVALID_AUTHENTICATION"}`. The message is the same text `api.hubapi.com` returned for an invalid token during development |
| Missing scope | 403 | `category: MISSING_SCOPES`, `errors[0].context.requiredGranularScopes` |
| Invalid input | 400 | `category: VALIDATION_ERROR`, HubSpot-style per-property error list |
| Unknown object | 404 | `category: OBJECT_NOT_FOUND` |
| Burst limit (default 150 / 10 s per token) | 429 | `errorType: RATE_LIMIT`, `policyName: TEN_SECONDLY_ROLLING` |
| Daily limit (default 250,000 per account) | 429 | `policyName: DAILY` |

Every authenticated response carries `X-HubSpot-RateLimit-Daily`, `-Daily-Remaining`, `-Interval-Milliseconds`, `-Max`, `-Remaining` and `X-HubSpot-Correlation-Id`.

## Mock-only admin endpoints
These do not exist in HubSpot; the resilience tests use them to provoke situations on demand.

| Endpoint | Purpose |
|---|---|
| `GET /__mock/health` | health check (used by Docker) |
| `GET /__mock/stats` | request counts by status, 429s returned, faults served, accounts |
| `PUT /__mock/config` | `{"rate_limit_per_10s": 20, "daily_limit": 1000, "latency_ms": 1000, "reset_usage": true}` |
| `POST /__mock/faults` | queue failures for `/crm/v3/objects/deals...`: `{"status": 503, "count": 8, "skip": 3}` or `{"status": 429, "policy": "DAILY"}` |
| `DELETE /__mock/faults` | clear queued failures |
| `POST /__mock/reset` | `{"account": "test"}` deletes all deals of an account |

## Not implemented
- Search (`POST /crm/v3/objects/deals/search`), batch endpoints and association data. The ETL does not use them.
- OAuth, multiple pipelines, custom properties and multi-currency.

Contract tests in `tests/test_mock_hubspot_contract.py` run the real `HubSpotAPIService` against the mock to keep it faithful to the behaviour above.
