# Resilience tests against the HubSpot mock (20261008194158)

**9/9 checks passed**

| Result | Check | Detail |
|---|---|---|
| PASS | Large volume: 2500 deals extracted across 25 HubSpot pages | 6.9s, 3 checkpointed batches |
| PASS | Large volume: results API pages through every row without duplicates | 5 pages of 500 |
| PASS | Rate limit: a 20 requests / 10 s quota is respected and the scan still completes | 51 requests in 28.8s (>= 20s), 0 x 429 received and retried, 2500 rows |
| PASS | Transient 502s are retried and the scan completes | 5 deals |
| PASS | HubSpot outage mid-scan marks the scan failed with the HubSpot error | 2 rows committed; error: HubSpot server error (503): An internal error occurred. Please try again later. |
| PASS | Failed scan resumes from its checkpoint after recovery without duplicates | 5 rows, recordsExtracted=5 |
| PASS | Daily limit 429 fails the scan without retrying | HubSpot rate limit exceeded (429): You have reached your daily limit. |
| PASS | Token without crm.objects.deals.read fails with a scope error | HubSpot access token is missing a required scope - deal extraction needs crm.objects.deals.read (403): This app hasn't been granted all required scopes to make this call. Read more about required scopes here: https://developers.hubspot.com/scopes. |
| PASS | archived=true extracts only archived deals with archived_at | 1 archived deal(s) |
