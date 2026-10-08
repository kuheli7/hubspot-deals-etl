"""
Create the 5 test deals in a HubSpot test account and record their IDs.

Requires a private app token with crm.objects.deals.read AND
crm.objects.deals.write in .env (HUBSPOT_ACCESS_TOKEN=pat-...).

    python scripts/create_test_deals.py

The script is idempotent: deals whose name already exists are reused instead of
duplicated. Results go to test-results/test_deals_created.json.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from scripts.common import get_access_token, load_env, write_json  # noqa: E402
from services.hubspot_api_service import HubSpotAPIError, HubSpotAPIService  # noqa: E402

TODAY = datetime.now(timezone.utc).replace(hour=17, minute=0, second=0, microsecond=0)

# stage_label is matched against the account's pipeline stages; stage_fallback
# is HubSpot's default internal ID used if the label lookup fails
TEST_DEALS = [
    {
        "dealname": "Acme Corp - Starter Plan",
        "amount": "5000",
        "stage_label": "Qualified To Buy", "stage_fallback": "qualifiedtobuy",
        "dealtype": "newbusiness",
        "hs_priority": "low",
        "closedate": TODAY + timedelta(days=30),
        "description": "Small team starter subscription. Qualified after discovery call; budget confirmed.",
    },
    {
        "dealname": "Globex - Annual Subscription",
        "amount": "25000",
        "stage_label": "Presentation Scheduled", "stage_fallback": "presentationscheduled",
        "dealtype": "newbusiness",
        "hs_priority": "medium",
        "closedate": TODAY + timedelta(days=45),
        "description": "Annual subscription for the analytics team. Product demo booked with the VP of Data.",
    },
    {
        "dealname": "Initech - Enterprise Expansion",
        "amount": "50000",
        "stage_label": "Closed Won", "stage_fallback": "closedwon",
        "dealtype": "existingbusiness",
        "hs_priority": "high",
        "closedate": TODAY - timedelta(days=10),
        "description": "Upsell of 200 additional seats for an existing customer. Contract signed.",
    },
    {
        "dealname": "Umbrella Health - Platform Migration",
        "amount": "75000",
        "stage_label": "Closed Lost", "stage_fallback": "closedlost",
        "dealtype": "newbusiness",
        "hs_priority": "medium",
        "closedate": TODAY - timedelta(days=5),
        "description": "Migration project from a legacy platform. Lost on price to a competitor.",
        "closed_lost_reason": "Chose a lower-cost competitor",
    },
    {
        "dealname": "Stark Industries - Multi-year Contract",
        "amount": "100000",
        "stage_label": "Contract Sent", "stage_fallback": "contractsent",
        "dealtype": "newbusiness",
        "hs_priority": "high",
        "closedate": TODAY + timedelta(days=60),
        "description": "Three-year enterprise agreement. Contract sent to legal for review.",
    },
]


def resolve_stages(api: HubSpotAPIService, token: str):
    """Map stage labels to internal IDs for the default deal pipeline"""
    pipelines = api.get_deal_pipelines(token)
    pipeline = next((p for p in pipelines if p.get("id") == "default"), pipelines[0] if pipelines else None)
    if not pipeline:
        return "default", {}
    stages = {s["label"].strip().lower(): s["id"] for s in pipeline.get("stages", [])}
    return pipeline["id"], stages


def main() -> int:
    env = load_env()
    token = get_access_token(env)
    api = HubSpotAPIService(base_url=env.get("HUBSPOT_API_BASE_URL", "https://api.hubapi.com"))

    validation = api.validate_credentials(token)
    if not validation["has_deals_read_scope"]:
        print(f"Token check failed: {validation['message']}")
        return 1

    pipeline_id, stages = resolve_stages(api, token)
    existing = {d["properties"].get("dealname"): d for d in api.iterate_deals(token, properties=["dealname"])}

    created = []
    for spec in TEST_DEALS:
        stage_id = stages.get(spec["stage_label"].lower(), spec["stage_fallback"])
        properties = {
            "dealname": spec["dealname"],
            "amount": spec["amount"],
            "pipeline": pipeline_id,
            "dealstage": stage_id,
            "dealtype": spec["dealtype"],
            "hs_priority": spec["hs_priority"],
            "closedate": spec["closedate"].strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "description": spec["description"],
        }
        if spec.get("closed_lost_reason"):
            properties["closed_lost_reason"] = spec["closed_lost_reason"]

        if spec["dealname"] in existing:
            deal = existing[spec["dealname"]]
            action = "existing"
        else:
            try:
                deal = api.create_deal(token, properties)
            except HubSpotAPIError as e:
                print(f"Failed to create '{spec['dealname']}': {e.message}")
                if e.status_code == 403:
                    print("The private app needs the crm.objects.deals.write scope to create deals.")
                return 1
            action = "created"

        print(f"{action:8} {deal['id']}  {spec['dealname']}  ${int(spec['amount']):,}  stage={stage_id}")
        created.append({
            "id": deal["id"],
            "action": action,
            "dealname": spec["dealname"],
            "amount": spec["amount"],
            "dealstage": stage_id,
            "stage_label": spec["stage_label"],
            "pipeline": pipeline_id,
            "dealtype": spec["dealtype"],
            "hs_priority": spec["hs_priority"],
            "closedate": properties["closedate"],
            "description": spec["description"],
        })

    path = write_json("test_deals_created.json", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": pipeline_id,
        "pipeline_stages": stages,
        "deals": created,
    })
    print(f"\nRecorded {len(created)} deal IDs in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
