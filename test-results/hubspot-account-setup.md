# HubSpot account setup (Phase 2)

## What happened with the HubSpot developer account
1. Sign-up for a HubSpot developer account was started at developers.hubspot.com, as Task 2.1 requires.
2. HubSpot asked for **government-ID verification** through Persona before the developer portal could be used.
3. This was raised with the project manager in the internship Teams chat. The manager's reply on **2026-10-09**: *"mock should work as long as you are matching the rest endpoints of hubspot"*.
4. A local HubSpot API mock was built that reproduces the HubSpot CRM v3 deals REST API ([`mock_hubspot/`](../mock_hubspot/README.md)). All Phase 2 and Phase 3 evidence in this folder was produced against it.

The ETL service is unchanged between the mock and real HubSpot. The only differences are `HUBSPOT_API_BASE_URL` and the access token.

## Account used for the tests (mock)
| Item | Value |
|---|---|
| Account | Deals ETL Test Account |
| Account (portal) ID | `48600123` (`GET /account-info/v3/details`, `accountType: DEVELOPER_TEST`) |
| Private app equivalent | "DLT Deals Extractor": token `pat-mock-test-account-deals` |
| Scopes granted | `crm.objects.deals.read` (extraction), `crm.objects.deals.write` (only to create the 5 test deals) |
| Additional tokens | read-only, no-scope and load-test tokens (see [`mock_hubspot/README.md`](../mock_hubspot/README.md#accounts-and-tokens)) for 403 and volume tests |
| Load-test account | Deals ETL Load Test Account (`48600456`) with 2,500 generated deals |
| Rate limits | 150 requests / 10 s per token, 250,000 / day per account (configurable) |

## Where credentials live
- The access token is set as `HUBSPOT_ACCESS_TOKEN` in `.env`, which is **git-ignored**. `.env.example` only holds a placeholder.
- The ETL service never reads the token from its environment. Each scan request carries it, it is stored Fernet-encrypted in `jobs.config`, and it is shown as `***redacted***` in every API response and log.
- Test scripts scrub the token value from everything written to `test-results/`.

## Test deals (Task 2.2)
Created through `POST /crm/v3/objects/deals` by [`scripts/create_test_deals.py`](../scripts/create_test_deals.py). IDs and all fields are in [`test_deals_created.json`](test_deals_created.json).

| Deal ID | Deal name | Amount | Stage | Type | Close date |
|---|---|---|---|---|---|
| 40100000001 | Acme Corp - Starter Plan | $5,000 | Qualified To Buy | New Business | +30 days |
| 40100000002 | Globex - Annual Subscription | $25,000 | Presentation Scheduled | New Business | +45 days |
| 40100000003 | Initech - Enterprise Expansion | $50,000 | Closed Won | Existing Business | -10 days |
| 40100000004 | Umbrella Health - Platform Migration | $75,000 | Closed Lost (reason recorded) | New Business | -5 days |
| 40100000005 | Stark Industries - Multi-year Contract | $100,000 | Contract Sent | New Business | +60 days |

Each deal has a description and a priority (low / medium / high).

## Switching to a real HubSpot account
1. Create a private app named **DLT Deals Extractor** with `crm.objects.deals.read` (add `crm.objects.deals.write` only while seeding test deals).
2. In `.env`, set `HUBSPOT_API_BASE_URL=https://api.hubapi.com` and `HUBSPOT_ACCESS_TOKEN=pat-...`, and remove the `COMPOSE_FILE` line.
3. Run `docker compose up -d --build`, then `python scripts/create_test_deals.py` and `python scripts/run_extraction_test.py --restart-test --crash-test`.
4. Remove the write scope from the private app after seeding.
