# Testing - HubSpot Deals ETL

The test approach follows the GreenTree **[API Test Workflow guideline (TEST-GUIDELINES-V1)](https://github.com/greentreegroup/policy/blob/main/TEST-GUIDELINES-V1.md)**. This page maps each guideline section to the automated tests that cover it. Results from the latest runs are in [`test-results/`](../test-results/README.md).

## Test suites

| Suite | Guideline test type | How to run | Needs | Latest result |
|---|---|---|---|---|
| `tests/test_hubspot_api_service.py`, `tests/test_data_source.py`, `tests/test_config.py` | Unit (mocked HTTP, startup checks) | `pytest` | nothing | 27 passed |
| `tests/test_mock_hubspot_contract.py` | Contract tests that keep the HubSpot mock faithful | `pytest` | nothing | 11 passed |
| `tests/test_seeded_api.py` | **Seeded Data Tests** (§3) and edge cases (§7) | `pytest` | PostgreSQL on `localhost:5432` (skipped otherwise) | 63 passed |
| `scripts/run_extraction_test.py` | **Real Extraction Tests** (§4) and edge cases (§7) | `python scripts/run_extraction_test.py --restart-test --crash-test` | running stack plus HubSpot token (or the mock) | 45/45 checks |
| `scripts/run_mock_resilience_test.py` | Edge cases needing a misbehaving HubSpot (§7: volume, rate limits, downtime) | `python scripts/run_mock_resilience_test.py` | running stack with the HubSpot mock | 9/9 checks |
| `.github/workflows/tests.yml` | CI (§8.10) | every push and pull request | GitHub Actions with a PostgreSQL service | - |

`pytest` runs all five pytest files: **101 tests**.

### Test data (§8.4)
- **Seeded:** a dedicated database, `hubspot_deals_test`, is dropped and recreated per session and reseeded before every test (data isolation). It holds jobs in every state (`pending`, `running`, `completed` ×3, `cancelled`, `failed`, plus a second tenant) and deal rows for 0, 3 and 250 records.
- **Extraction:** 5 deals in the HubSpot test account, created through `POST /crm/v3/objects/deals` by `scripts/create_test_deals.py`. They vary in stage, amount and type; some have optional fields missing (owner, currency) and one has a lost reason.
- **Volume:** a 2,500-deal load-test account in the HubSpot mock.
- **Malformed:** unparseable numbers and dates in HubSpot values become `NULL` (`tests/test_data_source.py`), and malformed requests are covered in §7 below.

### HubSpot account
The HubSpot developer account required government-ID verification, so the "real extraction" suite runs against the local HubSpot API mock with the project manager's approval ([`test-results/hubspot-account-setup.md`](../test-results/hubspot-account-setup.md)). The same script runs unchanged against `api.hubapi.com` with a private app token.

## Endpoint mapping (§6)
The generator template names some endpoints differently from the guideline. The guideline paths are served as aliases of the same resources (verified by `test_guideline_status_path_alias_returns_same_body` and the "Guideline paths" end-to-end check):

| Guideline endpoint | Method | Service endpoint (primary) | Guideline alias served |
|---|---|---|---|
| `/api/v1/scan/start` | POST | `/api/v1/scan/start` | (same) |
| `/api/v1/scan/status/<job_id>` | GET | `/api/v1/scan/<scanId>/status` | ✅ `/api/v1/scan/status/<scanId>` |
| `/api/v1/scan/result/<job_id>` | GET | `/api/v1/results/<scanId>/result` | ✅ `/api/v1/scan/result/<scanId>` |
| `/api/v1/scan/cancel/<job_id>` | POST | `/api/v1/scan/<scanId>/cancel` | ✅ `/api/v1/scan/cancel/<scanId>` |
| `/api/v1/scan/remove/<job_id>` | DELETE | `/api/v1/scan/<scanId>/remove` | ✅ `/api/v1/scan/remove/<scanId>` |
| `/api/v1/jobs/jobs` | GET | `/api/v1/scan/list` | ✅ `/api/v1/jobs/jobs` |
| `/api/v1/jobs/statistics` | GET | `/api/v1/scan/statistics` | ✅ `/api/v1/jobs/statistics` |
| `/api/v1/health` | GET | `/api/v1/health` (+ `/health` for Docker) | (same) |

The job ID is the caller-chosen `scanId`. The guideline's `in_progress` status is called `running` here; `pause`/`resume` add `paused` and `resuming`.

## §3 Seeded Data Tests → `tests/test_seeded_api.py`
| Guideline step | Test(s) |
|---|---|
| 1. Clean test database, seed jobs and extracted data | `app` and `seeded` fixtures |
| 2. Status matches the seeded state and metadata | `test_status_matches_seeded_state` (×8 jobs: status, recordsExtracted, start/end times, duration, error), `test_guideline_status_path_alias_returns_same_body` |
| 3. Results match the seeded data, pagination | `test_results_match_seeded_rows`, `test_results_pagination_over_many_records` (250 rows, 3 pages), `test_results_for_completed_scan_with_zero_records`, `test_results_limit_validation` |
| 4. List all jobs, pagination and filtering | `test_list_jobs_includes_every_seeded_job`, `test_list_jobs_pagination_and_tenant_filter` |
| 5. Statistics reflect the seeded data (incl. average extraction time) | `test_statistics_reflect_seeded_jobs` |
| 6. Health check | `test_health_endpoints_report_healthy` |
| 7. Cancel a pending job → `cancelled` | `test_cancel_pending_job` |
| 8. Remove job data → 404 afterwards | `test_remove_job_and_its_data` (completed and cancelled jobs; other scans untouched), `test_remove_running_job_is_rejected` |

## §4 Real Extraction Tests → `scripts/run_extraction_test.py`
| Guideline step | Check(s) |
|---|---|
| 1. Valid credentials, dedicated test account | "Access token valid with crm.objects.deals.read" (`POST /api/v1/auth/validate`) |
| 2. Start extraction → 202 with job ID | "POST /scan/start returns 202 with the scan ID" |
| 3. Poll status until completed/failed | `wait_for()` polling; timeline `pending -> running -> completed` recorded |
| 4. Results: 200, format, fields, compared with source data, pagination | "All 5 test deals extracted", "Extracted fields match HubSpot values" (name, amount, stage, type, close date, tenant, scan), "recordsExtracted equals the number of deals in HubSpot", database checks |
| 5. Remove data → 200, then 404 | "DELETE /scan/remove removes the scan; status and results then return 404" |

Additional extraction checks: pause/resume from a checkpoint, crash recovery after the container is killed mid-scan, restart survival, database column types and indexes.

## §5 Common assertions
| Assertion | Where |
|---|---|
| Exact HTTP status codes (200/202/400/401/404/409) | every suite |
| Job status transitions | e2e timeline; pause → `paused` → `resuming` → `completed`; crash → `crashed` → `completed`; outage → `failed` → `completed` (resilience) |
| Schema and data integrity, completeness, correctness | seeded results tests; e2e per-deal field comparison; DB column types |
| Pagination (`limit`/`offset`, totals, `hasMore`) | `test_results_pagination_over_many_records`, `test_list_jobs_pagination_and_tenant_filter`, resilience volume test (2,500 rows, 5 pages of 500) |
| Statistics accuracy | `test_statistics_reflect_seeded_jobs` |
| Confirmation messages | cancel / remove tests assert `success` and the message text |
| Error message content | 404/409/400 tests assert message text; scan failures assert the HubSpot error (401, 403 scope, 429 daily, 503) |
| Headers | `Content-Type: application/json` (seeded); `X-HubSpot-RateLimit-*` (contract) |
| Basic performance | "API response time acceptable"; volume and rate-limit timings in the resilience report |
| Health status | seeded health test; e2e health checks |

## §7.2 Edge cases
| Edge case | Covered by | Result |
|---|---|---|
| Invalid / missing / malformed token | `test_start_rejects_invalid_bodies_with_field_errors` (missing, empty), e2e "validate invalid token → 401", "scan with invalid token fails with clear 401 error", contract `test_unknown_token_gets_hubspot_401_body` | 400 / 401, job `failed` with HubSpot message |
| Non-existent job ID | `test_unknown_job_id_returns_404` (status, result, cancel, remove; primary and guideline paths), e2e edge cases | 404 |
| Results of an incomplete job | `test_results_for_incomplete_scan_conflict` (pending, running), e2e "Results blocked (409) while scan is paused" | 409 |
| Cancelling a completed / failed job | `test_cancel_job_in_final_state_conflicts`, e2e "cancel completed scan → 409" | 409 |
| Concurrent requests for the same extraction | e2e "5 concurrent identical starts create exactly one job"; `test_start_with_existing_scan_id_conflicts` | one 202, others 409 |
| Large data volumes / pagination stress | `test_results_pagination_over_many_records` (250), resilience "Large volume" (2,500 deals, 25 HubSpot pages, results API 5×500) | consistent pages, no duplicates |
| HubSpot downtime / errors | resilience: transient 502s retried; persistent 503s → `failed` with details, then resumed from checkpoint | as expected |
| Malformed request body | `test_start_rejects_malformed_json`, `test_start_rejects_invalid_bodies_with_field_errors`, e2e malformed JSON | 400 with field errors |
| ID injection / manipulation | `test_injection_and_oversized_ids_are_harmless` (SQL, path traversal, NUL, script, NoSQL, 5,000 chars; data unchanged), e2e injection and 5,000-char ID | 400 / 404, no data change |
| Rate limiting by HubSpot | unit 429 retry tests; resilience 20 req/10 s quota; daily-limit 429 fails fast | completes, or fails with the rate-limit message |

## §8 Practices
- **Automation (§8.5):** pytest plus Python `requests` scripts; no manual steps.
- **Robust assertions (§8.6):** status codes, bodies, error messages, headers and database state.
- **State and cleanup (§8.7):** chained requests use the returned `scanId`; seeded tests reseed per test; end-to-end scans use unique run IDs and tenants; the remove step cleans up; HubSpot is mocked for unit and contract tests.
- **Observability (§8.8):** JSON service logs (`test-results/logs/`), per-check details in `test_run_summary.md`, mock request statistics in `resilience_test.json`.
- **Maintainability (§8.9):** descriptive test names, this mapping, tests versioned with the code.
- **CI (§8.10):** `.github/workflows/tests.yml` runs the 101 pytest tests against PostgreSQL and checks that the Docker stack builds and becomes healthy.
