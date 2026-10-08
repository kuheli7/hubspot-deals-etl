Schema: hubspot_deals_org_hubspot_test.deals

| column | type | nullable |
|---|---|---|
| id | character varying | NO |
| dealname | character varying | YES |
| amount | numeric(18,2) | YES |
| dealstage | character varying | YES |
| pipeline | character varying | YES |
| dealtype | character varying | YES |
| closedate | timestamp with time zone | YES |
| createdate | timestamp with time zone | YES |
| hs_lastmodifieddate | timestamp with time zone | YES |
| description | character varying | YES |
| hubspot_owner_id | character varying | YES |
| hs_object_id | bigint | YES |
| hs_priority | character varying | YES |
| hs_deal_stage_probability | double precision | YES |
| hs_forecast_amount | numeric(18,2) | YES |
| hs_projected_amount | numeric(18,2) | YES |
| amount_in_home_currency | numeric(18,2) | YES |
| deal_currency_code | character varying | YES |
| hs_is_closed | boolean | YES |
| hs_is_closed_won | boolean | YES |
| days_to_close | bigint | YES |
| hs_closed_amount | numeric(18,2) | YES |
| closed_lost_reason | character varying | YES |
| closed_won_reason | character varying | YES |
| hs_next_step | character varying | YES |
| hs_analytics_source | character varying | YES |
| num_associated_contacts | bigint | YES |
| num_contacted_notes | bigint | YES |
| notes_last_updated | timestamp with time zone | YES |
| archived | boolean | YES |
| created_at | timestamp with time zone | YES |
| updated_at | timestamp with time zone | YES |
| archived_at | timestamp with time zone | YES |
| _extracted_at | timestamp with time zone | NO |
| _scan_id | character varying | NO |
| _tenant_id | character varying | NO |
| _page_number | bigint | YES |
| _source_service | character varying | YES |
| _dlt_load_id | character varying | NO |
| _dlt_id | character varying | NO |

Indexes:
- `CREATE UNIQUE INDEX deals__dlt_id_key ON hubspot_deals_org_hubspot_test.deals USING btree (_dlt_id)`
- `CREATE UNIQUE INDEX deals_id_key ON hubspot_deals_org_hubspot_test.deals USING btree (id)`
- `CREATE INDEX idx_deals_createdate ON hubspot_deals_org_hubspot_test.deals USING btree (createdate)`
- `CREATE INDEX idx_deals_lastmodified ON hubspot_deals_org_hubspot_test.deals USING btree (hs_lastmodifieddate)`
- `CREATE INDEX idx_deals_scan ON hubspot_deals_org_hubspot_test.deals USING btree (_scan_id)`
- `CREATE INDEX idx_deals_tenant ON hubspot_deals_org_hubspot_test.deals USING btree (_tenant_id)`
- `CREATE INDEX idx_deals_tenant_closedate ON hubspot_deals_org_hubspot_test.deals USING btree (_tenant_id, closedate)`
- `CREATE INDEX idx_deals_tenant_stage ON hubspot_deals_org_hubspot_test.deals USING btree (_tenant_id, pipeline, dealstage)`

Rows loaded by this scan:

| id | dealname | amount | dealstage | closedate | _tenant_id |
|---|---|---|---|---|---|
| 40100000006 | Acme Corp - Starter Plan | 5000.00 | qualifiedtobuy | 2026-11-07 17:00:00+00:00 | org-hubspot-test |
| 40100000007 | Globex - Annual Subscription | 25000.00 | presentationscheduled | 2026-11-22 17:00:00+00:00 | org-hubspot-test |
| 40100000008 | Initech - Enterprise Expansion | 50000.00 | closedwon | 2026-09-28 17:00:00+00:00 | org-hubspot-test |
| 40100000009 | Umbrella Health - Platform Migration | 75000.00 | closedlost | 2026-10-03 17:00:00+00:00 | org-hubspot-test |
| 40100000010 | Stark Industries - Multi-year Contract | 100000.00 | contractsent | 2026-12-07 17:00:00+00:00 | org-hubspot-test |
