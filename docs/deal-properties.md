# HubSpot Deal Properties

Generated 2026-10-08 19:39 UTC from `GET /crm/v3/properties/deals` on the local HubSpot API mock (`mock_hubspot/`, modelled on HubSpot's default deal properties) by `scripts/export_deal_properties.py`.

**54 properties** (54 HubSpot-defined, 0 custom). Properties marked **extracted** are requested by default and stored as typed columns in `deals`; any other property can be added per scan with `filters.properties` and is stored as text.

## analyticsinformation

| Internal name | Label | Type | Field type | Extracted | Description |
|---|---|---|---|---|---|
| `hs_analytics_latest_source` | Latest Traffic Source | enumeration | select |  | Source of the latest session. |
| `hs_analytics_latest_source_timestamp` | Latest Traffic Source Date | datetime | date |  | Time of the latest session. |
| `hs_analytics_source` | Original Traffic Source | enumeration | select | **extracted** (text) | Original source of the associated contact. |
| `hs_analytics_source_data_1` | Original Traffic Source Drill-Down 1 | string | text |  | Additional source detail. |
| `hs_analytics_source_data_2` | Original Traffic Source Drill-Down 2 | string | text |  | Additional source detail. |

## dealactivity

| Internal name | Label | Type | Field type | Extracted | Description |
|---|---|---|---|---|---|
| `closed_lost_reason` | Closed lost reason | string | textarea | **extracted** (text) | Reason why this deal was lost. |
| `closed_won_reason` | Closed won reason | string | textarea | **extracted** (text) | Reason why this deal was won. |
| `engagements_last_meeting_booked` | Date of last meeting booked in meetings tool | datetime | date |  | Date of the last meeting booked. |
| `hs_is_closed` | Is Deal Closed? | bool | booleancheckbox | **extracted** (bool) | True if the deal is in a closed stage. |
| `hs_is_closed_lost` | Is closed lost | bool | booleancheckbox |  | True if the deal is closed lost. |
| `hs_is_closed_won` | Is Closed Won | bool | booleancheckbox | **extracted** (bool) | True if the deal is closed won. |
| `hs_latest_approval_status` | Latest Approval Status | enumeration | select |  | The latest pipeline approval status. |
| `hubspot_owner_assigneddate` | Owner Assigned Date | datetime | date |  | The most recent timestamp of when an owner was assigned. |
| `notes_last_contacted` | Last Contacted | datetime | date |  | The last time a call, email or meeting was logged. |
| `notes_last_updated` | Last Activity Date | datetime | date | **extracted** (timestamp) | The last time a logged activity occurred. |
| `notes_next_activity_date` | Next Activity Date | datetime | date |  | The date of the next upcoming activity. |
| `num_contacted_notes` | Number of times contacted | number | number | **extracted** (bigint) | The number of times a call, chat, email or meeting was logged. |
| `num_notes` | Number of Sales Activities | number | number |  | The total number of sales activities. |

## dealinformation

| Internal name | Label | Type | Field type | Extracted | Description |
|---|---|---|---|---|---|
| `amount` | Amount | number | number | **extracted** (decimal) | The total amount of the deal. |
| `closedate` | Close Date | datetime | date | **extracted** (timestamp) | Date the deal was closed or is expected to close. |
| `createdate` | Create Date | datetime | date | **extracted** (timestamp) | Date the deal was created. |
| `days_to_close` | Days to close | number | calculation_equation | **extracted** (bigint) | Days between create date and close date. |
| `dealname` | Deal Name | string | text | **extracted** (text) | The name given to this deal. |
| `dealstage` | Deal Stage | enumeration | radio | **extracted** (text) | The stage of the deal. |
| `dealtype` | Deal Type | enumeration | radio | **extracted** (text) | The type of deal. |
| `description` | Deal Description | string | textarea | **extracted** (text) | Description of the deal |
| `hs_all_collaborator_owner_ids` | Deal collaborator | enumeration | checkbox |  | Users that are collaborating on this deal. |
| `hs_created_by_user_id` | Created by user ID | number | number |  | The user who created this record. |
| `hs_deal_score` | Deal score | number | number |  | Predictive deal health score. |
| `hs_deal_stage_probability` | Deal probability | number | number | **extracted** (double) | The probability a deal will close, based on the deal stage. |
| `hs_forecast_amount` | Forecast amount | number | calculation_equation | **extracted** (decimal) | Amount multiplied by forecast probability. |
| `hs_forecast_probability` | Forecast probability | number | number |  | Custom probability for forecasting. |
| `hs_lastmodifieddate` | Last Modified Date | datetime | date | **extracted** (timestamp) | Most recent timestamp of any property update. |
| `hs_manual_forecast_category` | Forecast category | enumeration | select |  | The likelihood a deal will close. |
| `hs_merged_object_ids` | Merged Deal IDs | enumeration | checkbox |  | Record IDs of deals merged into this deal. |
| `hs_next_step` | Next step | string | textarea | **extracted** (text) | A short description of the next step for the deal. |
| `hs_object_id` | Record ID | number | number | **extracted** (bigint) | The unique ID for this record. This value is set automatically by HubSpot. |
| `hs_object_source` | Record source | string | text |  | How this record was created. |
| `hs_priority` | Priority | enumeration | select | **extracted** (text) | The level of attention a deal requires. |
| `hs_projected_amount` | Weighted amount | number | calculation_equation | **extracted** (decimal) | Amount multiplied by deal probability. |
| `hs_tag_ids` | Deal tags | enumeration | checkbox |  | Tags applied to the deal. |
| `hs_updated_by_user_id` | Updated by user ID | number | number |  | The user who last updated this record. |
| `hubspot_owner_id` | Deal owner | enumeration | select | **extracted** (text) | User the deal is assigned to. |
| `hubspot_team_id` | HubSpot Team | enumeration | select |  | The primary team of the deal owner. |
| `num_associated_contacts` | Number of Associated Contacts | number | number | **extracted** (bigint) | The number of contacts associated with this deal. |
| `pipeline` | Pipeline | enumeration | select | **extracted** (text) | The pipeline the deal is in. |

## dealrevenue

| Internal name | Label | Type | Field type | Extracted | Description |
|---|---|---|---|---|---|
| `amount_in_home_currency` | Amount in company currency | number | calculation_equation | **extracted** (decimal) | The amount of the deal in the company currency. |
| `deal_currency_code` | Currency | enumeration | select | **extracted** (text) | Currency code for the deal. |
| `hs_acv` | Annual contract value | number | calculation_equation |  | Annual contract value from line items. |
| `hs_arr` | Annual recurring revenue | number | calculation_equation |  | Annual recurring revenue from line items. |
| `hs_closed_amount` | Closed amount | number | calculation_equation | **extracted** (decimal) | Amount of closed won deals. |
| `hs_exchange_rate` | Exchange rate | number | number |  | Exchange rate used to convert the amount. |
| `hs_mrr` | Monthly recurring revenue | number | calculation_equation |  | Monthly recurring revenue from line items. |
| `hs_tcv` | Total contract value | number | calculation_equation |  | Total contract value from line items. |

## HubSpot type → PostgreSQL type

| HubSpot `type` | PostgreSQL |
|---|---|
| string | varchar |
| phone_number | varchar |
| enumeration | varchar |
| number | numeric / bigint / double precision |
| date | timestamptz |
| datetime | timestamptz |
| bool | boolean |
