# Test results

The files in this folder are the output of the test runs on 2026-10-08/09 (UTC). They were produced against the **local HubSpot API mock** ([`mock_hubspot/`](../mock_hubspot/README.md)), with the project manager's approval, because the HubSpot developer account required ID verification (see [`hubspot-account-setup.md`](hubspot-account-setup.md)). All files were written by the test scripts. The exceptions are `docker_compose_ps.txt`, which was regenerated with `docker compose ps` after the run to fix a character-encoding problem, and the two hand-written summaries (`README.md`, `hubspot-account-setup.md`). Tokens are scrubbed. Which HubSpot behaviours the mock verifies versus models is listed in [`mock_hubspot/README.md`](../mock_hubspot/README.md#how-faithful-is-it).

## Summary
| Suite | Result | Evidence |
|---|---|---|
| `pytest`: unit (27), mock contract (11), **seeded data** (63, guideline §3) | **101 / 101 passed** | [`pytest_results.txt`](pytest_results.txt) |
| End-to-end extraction (`scripts/run_extraction_test.py --restart-test --crash-test --mock-latency-ms 1000`) | **45 / 45 checks passed** | [`test_run_summary.md`](test_run_summary.md) |
| Resilience (`scripts/run_mock_resilience_test.py`) | **9 / 9 checks passed** | [`resilience_test.md`](resilience_test.md) |

## Key outcomes
| Requirement | Result |
|---|---|
| Docker services start without errors | 4 containers healthy ([`docker_compose_ps.txt`](docker_compose_ps.txt)); also verified from a fresh clone (now run `cp .env.example .env` first: the stack requires `CONFIG_PASSWORD` and `COORDINATOR_KEY`) |
| Health check | `GET /health` → 200 `healthy` ([`health_check.json`](health_check.json)) |
| API documentation | Swagger UI at `/docs/` → 200; OpenAPI lists 24 paths |
| 5 test deals created | IDs 40100000001–40100000005 ([`test_deals_created.json`](test_deals_created.json)) |
| All 5 deals extracted | 5/5 found, every field matches ([`deal_verification.json`](deal_verification.json), [`extraction_results.json`](extraction_results.json)) |
| Database format | `numeric(18,2)` amounts, `timestamptz` dates, metadata columns, 6 indexes + unique `id` ([`database_verification.md`](database_verification.md)) |
| Checkpointing (pause → resume) | Paused after page 2 with 2 rows committed; resumed from cursor; 5 rows, no duplicates ([`checkpoint_test.json`](checkpoint_test.json)) |
| Interruption (container killed mid-scan) | Detected as `crashed`, resumed from checkpoint; 5 rows, no duplicates ([`crash_recovery_test.json`](crash_recovery_test.json)) |
| Restart | Service healthy again after a container restart; scans and results intact ([`restart_test.json`](restart_test.json)) |
| Response time | Full scan of the test account completed in 1.5 s |
| Volume | 2,500 deals over 25 pages in 9.8 s, 3 checkpointed batches |
| Rate limits | 20 req/10 s quota respected (51 requests in 36 s, no failed requests); daily-limit 429 fails fast |
| Concurrent starts | 5 identical simultaneous starts: one 202, four 409, a single job ([`concurrency_test.json`](concurrency_test.json)) |
| Guideline paths | `/scan/status`, `/scan/result`, `/jobs/jobs`, `/jobs/statistics` identical to primary routes ([`guideline_paths.json`](guideline_paths.json)) |
| Cleanup (guideline §4.5) | `DELETE /scan/remove/{id}` → 200, then status and results 404, 0 rows left ([`remove_test.json`](remove_test.json)) |
| Seeded data (guideline §3) | Status, results (0/3/250 rows), list, statistics, cancel, remove and injection tests on a seeded test database ([`pytest_results.txt`](pytest_results.txt)) |
| Outages | Transient 502s retried; persistent 503s fail the scan with HubSpot's error, and it resumes from its checkpoint afterwards |
| Edge cases | Invalid/missing token, malformed JSON, injection and 5,000-char IDs, unknown IDs, duplicate scan, wrong-state cancel, limits ([`edge_cases.json`](edge_cases.json)) |

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
| `pytest_results.txt` | `pytest -v` output (unit, contract, seeded data) |
| `concurrency_test.json` | Concurrent identical start requests |
| `guideline_paths.json` | Guideline path aliases compared with primary routes |
| `remove_test.json` | Remove scan, then 404 and no rows left |
| `docker_compose_ps.txt` | Container status during the run |
| `logs/service_test_run.log` | Service logs (INFO and above) during the end-to-end run |
