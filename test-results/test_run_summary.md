# Extraction test run 20261008201015

Started 2026-10-08T20:10:15.038044+00:00 - finished 2026-10-08T20:12:27.270756+00:00

**45/45 checks passed**

| Result | Check | Detail |
|---|---|---|
| PASS | GET /health returns 200 healthy | 47.0 ms |
| PASS | GET /api/v1/health returns healthy |  |
| PASS | Swagger UI served at /docs/ |  |
| PASS | OpenAPI spec lists the deal endpoints | 24 paths |
| PASS | Access token valid with crm.objects.deals.read | rate limit headers: {'daily_limit': '250000', 'daily_remaining': '249989', 'interval_milliseconds': '10000', 'interval_max': '150', 'interval_remaining': '146', 'observed_at': '2026-10-08T20:10:15.382843+00:00'} |
| PASS | POST /scan/start returns 202 with the scan ID | HTTP 202, data={'scanId': 'deals-e2e-20261008201015', 'organizationId': 'org-hubspot-test', 'status': 'pending', 'statusUrl': '/api/v1/scan/deals-e2e-20261008201015/status', 'resultUrl': '/api/v1/results/deals-e2e-20261008201015/result'} |
| PASS | Scan completed | timeline=pending -> running -> completed, 1.48s, error=None |
| PASS | All 5 test deals extracted | 5/5 found |
| PASS | Extracted fields match HubSpot values |  |
| PASS | recordsExtracted equals the number of deals in HubSpot | HubSpot=5, recordsExtracted=5, rows=5 |
| PASS | API response time acceptable (< 120 s end-to-end) | 1.48s |
| PASS | deals table holds one row per HubSpot deal | 5 rows for 5 deals in HubSpot |
| PASS | All recorded test deal IDs are in the database |  |
| PASS | No duplicate deal IDs |  |
| PASS | amount stored as numeric(18,2) |  |
| PASS | closedate stored as timestamptz |  |
| PASS | ETL metadata columns present |  |
| PASS | Tenant/date/stage indexes created | ['deals__dlt_id_key', 'deals_id_key', 'idx_deals_createdate', 'idx_deals_lastmodified', 'idx_deals_scan', 'idx_deals_tenant', 'idx_deals_tenant_closedate', 'idx_deals_tenant_stage'] |
| PASS | Scan paused mid-run with committed checkpoint cursor | paused at page 2 with 2 rows loaded |
| PASS | Results blocked (409) while scan is paused |  |
| PASS | Resume accepted (202) | Job deals-checkpoint-20261008201015 is resuming from checkpoint |
| PASS | Resumed scan completed with all deals and no duplicates | 5 rows, 5 distinct, recordsExtracted=5 |
| PASS | Resume continued from checkpoint instead of restarting | ['deals_batch_committed@1', 'deals_paused@2', 'deals_batch_committed@3', 'deals_batch_committed@4', 'deals_completed@5'] |
| PASS | Interrupted scan detected as crashed | 1 rows were committed before the container restart |
| PASS | Crashed scan resumed from its checkpoint and completed without loss or duplicates | 5 rows, 5 distinct, recordsExtracted=5 |
| PASS | Edge case: validate invalid token -> 401 | HTTP 401 |
| PASS | Edge case: scan with invalid token fails with clear 401 error | HubSpot rejected the access token (401): Authentication credentials not found. This API supports OAuth 2.0 authentication and you can find more details at https://developers.hubspot.com/docs/methods/auth/oauth-overview |
| PASS | Edge case: missing token -> 400 | HTTP 400 |
| PASS | Edge case: malformed JSON -> 400 | HTTP 400 |
| PASS | Edge case: unsupported type -> 400 | HTTP 400 |
| PASS | Edge case: SQL injection in organizationId -> 400 | HTTP 400 |
| PASS | Edge case: duplicate scanId -> 409 | HTTP 409 |
| PASS | Edge case: status of unknown scan -> 404 | HTTP 404 |
| PASS | Edge case: results of unknown scan -> 404 | HTTP 404 |
| PASS | Edge case: cancel unknown scan -> 404 | HTTP 404 |
| PASS | Edge case: remove unknown scan -> 404 | HTTP 404 |
| PASS | Edge case: cancel completed scan -> 409 | HTTP 409 |
| PASS | Edge case: results limit above maximum -> 400 | HTTP 400 |
| PASS | Edge case: 5,000-character job ID -> 404 | HTTP 404 |
| PASS | Edge case: SQL injection in job ID -> 404 | HTTP 404 |
| PASS | 5 concurrent identical starts create exactly one job (one 202, four 409) | status codes [202, 409, 409, 409, 409], job completed |
| PASS | Guideline paths (/scan/status, /scan/result, /jobs/jobs, /jobs/statistics) answer like the primary routes | ['/scan/status/deals-e2e-20261008201015', '/scan/result/deals-e2e-20261008201015', '/jobs/jobs?limit=5', '/jobs/statistics'] |
| PASS | Service restarts cleanly and is healthy again | 13.3s |
| PASS | Scans and results survive restart |  |
| PASS | DELETE /scan/remove removes the scan; status and results then return 404 | remove 200, status 404, result 404, rows left 0 |
