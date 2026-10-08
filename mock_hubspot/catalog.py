"""
Static HubSpot account configuration served by the mock: deal property
definitions (GET /crm/v3/properties/deals) and deal pipelines
(GET /crm/v3/pipelines/deals).

Property names, labels, types and groups follow HubSpot's default deal
properties (https://knowledge.hubspot.com/properties/hubspots-default-deal-properties).
"""
from typing import Any, Dict, List, Optional

CREATED_AT = "2025-01-01T00:00:00Z"

# (name, label, type, fieldType, groupName, calculated/read-only, options, description)
_DEAL_PROPERTIES = [
    # Deal information
    ("dealname", "Deal Name", "string", "text", "dealinformation", False, None, "The name given to this deal."),
    ("description", "Deal Description", "string", "textarea", "dealinformation", False, None, "Description of the deal"),
    ("hubspot_owner_id", "Deal owner", "enumeration", "select", "dealinformation", False, [], "User the deal is assigned to."),
    ("dealtype", "Deal Type", "enumeration", "radio", "dealinformation", False,
     [("New Business", "newbusiness"), ("Existing Business", "existingbusiness")], "The type of deal."),
    ("closedate", "Close Date", "datetime", "date", "dealinformation", False, None, "Date the deal was closed or is expected to close."),
    ("createdate", "Create Date", "datetime", "date", "dealinformation", False, None, "Date the deal was created."),
    ("hs_created_by_user_id", "Created by user ID", "number", "number", "dealinformation", True, None, "The user who created this record."),
    ("hs_updated_by_user_id", "Updated by user ID", "number", "number", "dealinformation", True, None, "The user who last updated this record."),
    ("hs_object_id", "Record ID", "number", "number", "dealinformation", True, None, "The unique ID for this record. This value is set automatically by HubSpot."),
    ("hs_object_source", "Record source", "string", "text", "dealinformation", True, None, "How this record was created."),
    ("hs_priority", "Priority", "enumeration", "select", "dealinformation", False,
     [("Low", "low"), ("Medium", "medium"), ("High", "high")], "The level of attention a deal requires."),
    ("hs_deal_stage_probability", "Deal probability", "number", "number", "dealinformation", True, None, "The probability a deal will close, based on the deal stage."),
    ("hs_projected_amount", "Weighted amount", "number", "calculation_equation", "dealinformation", True, None, "Amount multiplied by deal probability."),
    ("hs_forecast_amount", "Forecast amount", "number", "calculation_equation", "dealinformation", True, None, "Amount multiplied by forecast probability."),
    ("hs_manual_forecast_category", "Forecast category", "enumeration", "select", "dealinformation", False,
     [("Not forecasted", "OMIT"), ("Pipeline", "PIPELINE"), ("Best case", "BEST_CASE"), ("Commit", "COMMIT"), ("Closed won", "CLOSED")],
     "The likelihood a deal will close."),
    ("hs_forecast_probability", "Forecast probability", "number", "number", "dealinformation", False, None, "Custom probability for forecasting."),
    ("hs_next_step", "Next step", "string", "textarea", "dealinformation", False, None, "A short description of the next step for the deal."),
    ("hs_deal_score", "Deal score", "number", "number", "dealinformation", True, None, "Predictive deal health score."),
    ("hs_all_collaborator_owner_ids", "Deal collaborator", "enumeration", "checkbox", "dealinformation", False, [], "Users that are collaborating on this deal."),
    ("hs_tag_ids", "Deal tags", "enumeration", "checkbox", "dealinformation", True, [], "Tags applied to the deal."),
    ("hubspot_team_id", "HubSpot Team", "enumeration", "select", "dealinformation", True, [], "The primary team of the deal owner."),
    ("hs_merged_object_ids", "Merged Deal IDs", "enumeration", "checkbox", "dealinformation", True, [], "Record IDs of deals merged into this deal."),
    ("num_associated_contacts", "Number of Associated Contacts", "number", "number", "dealinformation", True, None, "The number of contacts associated with this deal."),
    # Deal activity
    ("dealstage", "Deal Stage", "enumeration", "radio", "dealinformation", False, "stages", "The stage of the deal."),
    ("pipeline", "Pipeline", "enumeration", "select", "dealinformation", False, "pipelines", "The pipeline the deal is in."),
    ("hs_is_closed", "Is Deal Closed?", "bool", "booleancheckbox", "dealactivity", True, [("True", "true"), ("False", "false")], "True if the deal is in a closed stage."),
    ("hs_is_closed_won", "Is Closed Won", "bool", "booleancheckbox", "dealactivity", True, [("True", "true"), ("False", "false")], "True if the deal is closed won."),
    ("hs_is_closed_lost", "Is closed lost", "bool", "booleancheckbox", "dealactivity", True, [("True", "true"), ("False", "false")], "True if the deal is closed lost."),
    ("closed_won_reason", "Closed won reason", "string", "textarea", "dealactivity", False, None, "Reason why this deal was won."),
    ("closed_lost_reason", "Closed lost reason", "string", "textarea", "dealactivity", False, None, "Reason why this deal was lost."),
    ("notes_last_updated", "Last Activity Date", "datetime", "date", "dealactivity", True, None, "The last time a logged activity occurred."),
    ("notes_last_contacted", "Last Contacted", "datetime", "date", "dealactivity", True, None, "The last time a call, email or meeting was logged."),
    ("notes_next_activity_date", "Next Activity Date", "datetime", "date", "dealactivity", True, None, "The date of the next upcoming activity."),
    ("hs_lastmodifieddate", "Last Modified Date", "datetime", "date", "dealinformation", True, None, "Most recent timestamp of any property update."),
    ("num_notes", "Number of Sales Activities", "number", "number", "dealactivity", True, None, "The total number of sales activities."),
    ("num_contacted_notes", "Number of times contacted", "number", "number", "dealactivity", True, None, "The number of times a call, chat, email or meeting was logged."),
    ("hubspot_owner_assigneddate", "Owner Assigned Date", "datetime", "date", "dealactivity", True, None, "The most recent timestamp of when an owner was assigned."),
    ("hs_latest_approval_status", "Latest Approval Status", "enumeration", "select", "dealactivity", True, [], "The latest pipeline approval status."),
    ("engagements_last_meeting_booked", "Date of last meeting booked in meetings tool", "datetime", "date", "dealactivity", True, None, "Date of the last meeting booked."),
    # Deal revenue
    ("amount", "Amount", "number", "number", "dealinformation", False, None, "The total amount of the deal."),
    ("amount_in_home_currency", "Amount in company currency", "number", "calculation_equation", "dealrevenue", True, None, "The amount of the deal in the company currency."),
    ("deal_currency_code", "Currency", "enumeration", "select", "dealrevenue", False, [("US Dollar", "USD")], "Currency code for the deal."),
    ("hs_exchange_rate", "Exchange rate", "number", "number", "dealrevenue", True, None, "Exchange rate used to convert the amount."),
    ("hs_acv", "Annual contract value", "number", "calculation_equation", "dealrevenue", True, None, "Annual contract value from line items."),
    ("hs_arr", "Annual recurring revenue", "number", "calculation_equation", "dealrevenue", True, None, "Annual recurring revenue from line items."),
    ("hs_mrr", "Monthly recurring revenue", "number", "calculation_equation", "dealrevenue", True, None, "Monthly recurring revenue from line items."),
    ("hs_tcv", "Total contract value", "number", "calculation_equation", "dealrevenue", True, None, "Total contract value from line items."),
    ("hs_closed_amount", "Closed amount", "number", "calculation_equation", "dealrevenue", True, None, "Amount of closed won deals."),
    ("days_to_close", "Days to close", "number", "calculation_equation", "dealinformation", True, None, "Days between create date and close date."),
    # Analytics history
    ("hs_analytics_source", "Original Traffic Source", "enumeration", "select", "analyticsinformation", True,
     [("Organic Search", "ORGANIC_SEARCH"), ("Paid Search", "PAID_SEARCH"), ("Email Marketing", "EMAIL_MARKETING"),
      ("Direct Traffic", "DIRECT_TRAFFIC"), ("Offline Sources", "OFFLINE")], "Original source of the associated contact."),
    ("hs_analytics_source_data_1", "Original Traffic Source Drill-Down 1", "string", "text", "analyticsinformation", True, None, "Additional source detail."),
    ("hs_analytics_source_data_2", "Original Traffic Source Drill-Down 2", "string", "text", "analyticsinformation", True, None, "Additional source detail."),
    ("hs_analytics_latest_source", "Latest Traffic Source", "enumeration", "select", "analyticsinformation", True, [], "Source of the latest session."),
    ("hs_analytics_latest_source_timestamp", "Latest Traffic Source Date", "datetime", "date", "analyticsinformation", True, None, "Time of the latest session."),
]

# Default sales pipeline with HubSpot's default internal stage IDs and probabilities
PIPELINES: List[Dict[str, Any]] = [
    {
        "id": "default",
        "label": "Sales Pipeline",
        "displayOrder": 0,
        "stages": [
            ("appointmentscheduled", "Appointment Scheduled", "0.2", False),
            ("qualifiedtobuy", "Qualified To Buy", "0.4", False),
            ("presentationscheduled", "Presentation Scheduled", "0.6", False),
            ("decisionmakerboughtin", "Decision Maker Bought-In", "0.8", False),
            ("contractsent", "Contract Sent", "0.9", False),
            ("closedwon", "Closed Won", "1.0", True),
            ("closedlost", "Closed Lost", "0.0", True),
        ],
    }
]

# Properties HubSpot returns from the list endpoint when no `properties` are requested
DEFAULT_RETURNED_PROPERTIES = [
    "amount", "closedate", "createdate", "dealname", "dealstage",
    "hs_lastmodifieddate", "hs_object_id", "pipeline",
]
# Properties included in every response even when not requested
ALWAYS_RETURNED_PROPERTIES = ["createdate", "hs_lastmodifieddate", "hs_object_id"]


def stage_index() -> Dict[str, Dict[str, Any]]:
    """stage id -> {pipeline, label, probability, is_closed}"""
    index = {}
    for pipeline in PIPELINES:
        for stage_id, label, probability, is_closed in pipeline["stages"]:
            index[stage_id] = {
                "pipeline": pipeline["id"],
                "label": label,
                "probability": probability,
                "is_closed": is_closed,
            }
    return index


def pipeline_payload(pipeline: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "label": pipeline["label"],
        "displayOrder": pipeline["displayOrder"],
        "id": pipeline["id"],
        "stages": [
            {
                "label": label,
                "displayOrder": order,
                "metadata": {"isClosed": "true" if is_closed else "false", "probability": probability},
                "id": stage_id,
                "createdAt": CREATED_AT,
                "updatedAt": CREATED_AT,
                "archived": False,
            }
            for order, (stage_id, label, probability, is_closed) in enumerate(pipeline["stages"])
        ],
        "createdAt": CREATED_AT,
        "updatedAt": CREATED_AT,
        "archived": False,
    }


def _options(spec) -> List[Dict[str, Any]]:
    if spec == "stages":
        spec = [(label, stage_id) for p in PIPELINES for stage_id, label, _, _ in p["stages"]]
    elif spec == "pipelines":
        spec = [(p["label"], p["id"]) for p in PIPELINES]
    return [
        {"label": label, "value": value, "displayOrder": order, "hidden": False}
        for order, (label, value) in enumerate(spec or [])
    ]


PROPERTY_DEFINITIONS: Dict[str, Dict[str, Any]] = {}
for _order, (_name, _label, _type, _field, _group, _calc, _opts, _desc) in enumerate(_DEAL_PROPERTIES):
    PROPERTY_DEFINITIONS[_name] = {
        "updatedAt": CREATED_AT,
        "createdAt": CREATED_AT,
        "name": _name,
        "label": _label,
        "type": _type,
        "fieldType": _field,
        "description": _desc,
        "groupName": _group,
        "options": _options(_opts) if _opts is not None else [],
        "displayOrder": _order,
        "calculated": _calc,
        "externalOptions": _opts in ("stages", "pipelines"),
        "hasUniqueValue": _name == "hs_object_id",
        "hidden": False,
        "hubspotDefined": True,
        "modificationMetadata": {"archivable": False, "readOnlyDefinition": True, "readOnlyValue": _calc},
        "formField": not _calc,
        "dataSensitivity": "non_sensitive",
    }

READ_ONLY_PROPERTIES = {name for name, d in PROPERTY_DEFINITIONS.items() if d["calculated"]}


def property_definition(name: str) -> Optional[Dict[str, Any]]:
    return PROPERTY_DEFINITIONS.get(name)
