# Test results

The files in this folder are the output of the test runs on 2026-10-08/09 (UTC). They were produced against the **local HubSpot API mock** ([`mock_hubspot/`](../mock_hubspot/README.md)), with the project manager's approval, because the HubSpot developer account required ID verification (see [`hubspot-account-setup.md`](hubspot-account-setup.md)). Nothing here was edited by hand. Tokens are scrubbed.

## Summary
| Suite | Result | Evidence |
|---|---|---|
| Unit and mock-contract tests (`pytest`) | **33 / 33 passed** | [`unit_tests.txt`](unit_tests.txt) |
| End-to-end extraction (`scripts/run_extraction_test.py --restart-test --crash-test --mock-latency-ms 1000`) | **40 / 40 checks passed** | [`test_run_summary.md`](test_run_summary.md) |
| Resilience (`scripts/run_mock_resilience_test.py`) | **9 / 9 checks passed** | [`resilience_test.md`](resilience_test.md) |

## Key outcomes
| Requirement | Result |
|---|---|
| Docker services start without errors | 4 containers healthy ([`docker_compose_ps.txt`](docker_compose_ps.txt)); also verified from a fresh clone without `.env` |
| Health check | `GET /health` → 200 `healthy` ([`health_check.json`](health_check.json)) |
| API documentation | Swagger UI at `/docs/` → 200; OpenAPI lists 16 paths |
| 5 test deals created | IDs 40100000006–40100000010 ([`test_deals_created.json`](test_deals_created.json)) |
| All 5 deals extracted | 5/5 found, every field matches ([`deal_verification.json`](deal_verification.json), [`extraction_results.json`](extraction_results.json)) |
| Database format | `numeric(18,2)` amounts, `timestamptz` dates, metadata columns, 6 indexes + unique `id` ([`database_verification.md`](database_verification.md)) |
| Checkpointing (pause → resume) | Paused after page 2 with 2 rows committed; resumed from cursor; 5 rows, no duplicates ([`checkpoint_test.json`](checkpoint_test.json)) |
| Interruption (container killed mid-scan) | Detected as `crashed`, resumed from checkpoint; 5 rows, no duplicates ([`crash_recovery_test.json`](crash_recovery_test.json)) |
| Restart | Service healthy again in 10.8 s; scans and results intact ([`restart_test.json`](restart_test.json)) |
| Response time | Full scan of the test account completed in 8.8 s |
| Volume | 2,500 deals over 25 pages in 6.9 s, 3 checkpointed batches |
| Rate limits | 20 req/10 s quota respected (51 requests in 28.8 s, no failed requests); daily-limit 429 fails fast |
| Outages | Transient 502s retried; persistent 503s fail the scan with HubSpot's error, and it resumes from its checkpoint afterwards |
| Edge cases | Invalid/missing token, malformed JSON, injection, unknown IDs, duplicate scan, wrong-state cancel, limits ([`edge_cases.json`](edge_cases.json)) |

## Files
| File | Contents |
|---|---|
| `hubspot-account-setup.md` | Account, scopes, token handling, test deals |
| `test_deals_created.json` | The 5 deals as created through `POST /crm/v3/objects/deals` |
| `deal_properties.json` | `GET /crm/v3/properties/deals` response (also rendered to `docs/deal-properties.md`) |
| `credential_validation.json` | `POST /api/v1/auth/validate` result including rate-limit headers |
| `health_check.json` | `/health`, `/api/v1/health`, `/docs/`, OpenAPI paths |
| `extraction_results.json` | Scan status timeline, final job record, results API output |
| `deal_verification.json` | Per-deal, per-field comparison with the created deals |
| `database_verification.{json,md}` | Column types, indexes, rows, duplicate check, job and checkpoint rows from PostgreSQL |
| `checkpoint_test.json` | Pause/resume run with 1-deal pages and a checkpoint every page |
| `crash_recovery_test.json` | Container restart mid-scan → crash detection → resume |
| `restart_test.json` | Service restart, data survives |
| `edge_cases.json` | Expected vs. actual HTTP status for each edge case |
| `resilience_test.{json,md}` | Volume, rate limit, outage, daily limit, scope and archived-deal tests |
| `test_run_summary.{json,md}` | All end-to-end checks with details |
| `unit_tests.txt` | `pytest -v` output |
| `docker_compose_ps.txt` | Container status during the run |
| `logs/service_test_run.log` | Service logs (INFO and above) during the end-to-end run |
