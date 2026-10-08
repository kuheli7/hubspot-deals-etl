"""
Export every deal property defined in a HubSpot account (GET /crm/v3/properties/deals)
to docs/deal-properties.md and test-results/deal_properties.json.

    python scripts/export_deal_properties.py
"""
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from scripts.common import PROJECT_ROOT, get_access_token, load_env, write_json  # noqa: E402
from services.data_source import DEAL_COLUMN_HINTS  # noqa: E402
from services.hubspot_api_service import HubSpotAPIService  # noqa: E402

PG_TYPE_FOR_HUBSPOT = {
    "string": "varchar",
    "phone_number": "varchar",
    "enumeration": "varchar",
    "number": "numeric / bigint / double precision",
    "date": "timestamptz",
    "datetime": "timestamptz",
    "bool": "boolean",
}


def cell(text) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def main() -> int:
    env = load_env()
    token = get_access_token(env)
    api = HubSpotAPIService(base_url=env.get("HUBSPOT_API_BASE_URL", "https://api.hubapi.com"))
    properties = sorted(api.get_deal_properties(token), key=lambda p: (p.get("groupName", ""), p["name"]))

    write_json("deal_properties.json", {"count": len(properties), "properties": properties})

    by_group = defaultdict(list)
    for prop in properties:
        by_group[prop.get("groupName") or "other"].append(prop)

    hubspot_defined = sum(1 for p in properties if p.get("hubspotDefined"))
    lines = [
        "# HubSpot Deal Properties",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} from `GET /crm/v3/properties/deals` "
        "on the HubSpot test account by `scripts/export_deal_properties.py`.",
        "",
        f"**{len(properties)} properties** ({hubspot_defined} HubSpot-defined, "
        f"{len(properties) - hubspot_defined} custom). Properties marked **extracted** are requested by "
        "default and stored as typed columns in `deals`; any other property can be added per scan with "
        "`filters.properties` and is stored as text.",
        "",
    ]
    for group in sorted(by_group):
        lines += [f"## {group}", "", "| Internal name | Label | Type | Field type | Extracted | Description |",
                  "|---|---|---|---|---|---|"]
        for prop in by_group[group]:
            hint = DEAL_COLUMN_HINTS.get(prop["name"])
            extracted = f"**extracted** ({hint['data_type']})" if hint else ""
            lines.append(
                f"| `{prop['name']}` | {cell(prop.get('label'))} | {prop.get('type')} | {prop.get('fieldType')} "
                f"| {extracted} | {cell(prop.get('description'))[:160]} |"
            )
        lines.append("")
    lines += ["## HubSpot type → PostgreSQL type", "", "| HubSpot `type` | PostgreSQL |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in PG_TYPE_FOR_HUBSPOT.items()]

    out = PROJECT_ROOT / "docs" / "deal-properties.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(properties)} properties to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
