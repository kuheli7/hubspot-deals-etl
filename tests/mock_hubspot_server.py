"""
Minimal mock of the HubSpot CRM deals API for offline integration testing.

It is NOT used for the real extraction evidence in test-results/; it exists so
pagination, checkpointing, pause/resume and the PostgreSQL load can be tested
without a HubSpot account.

    python tests/mock_hubspot_server.py --port 5299 --deals 7
    # then set HUBSPOT_API_BASE_URL=http://host.docker.internal:5299 in .env

Accepted token: any Bearer token starting with "mock-". Pass --fail-first-429
to make the first deals request return 429 (exercises the retry path).
"""
import argparse
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

STAGES = ["qualifiedtobuy", "presentationscheduled", "closedwon", "closedlost", "appointmentscheduled"]
AMOUNTS = ["5000", "25000", "50000", "75000", "100000"]


def build_deals(count: int):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deals = []
    for i in range(count):
        created = base + timedelta(days=i)
        deals.append({
            "id": str(9000000001 + i),
            "properties": {
                "dealname": f"Mock Deal {i + 1}",
                "amount": AMOUNTS[i % len(AMOUNTS)],
                "dealstage": STAGES[i % len(STAGES)],
                "pipeline": "default",
                "dealtype": "newbusiness" if i % 2 == 0 else "existingbusiness",
                "closedate": (created + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "createdate": created.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "hs_lastmodifieddate": created.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "description": f"Mock description {i + 1}",
                "hs_object_id": str(9000000001 + i),
                "hs_deal_stage_probability": "0.4",
                "hs_is_closed": "true" if STAGES[i % len(STAGES)].startswith("closed") else "false",
                "hs_is_closed_won": "true" if STAGES[i % len(STAGES)] == "closedwon" else "false",
                "days_to_close": "30",
            },
            "createdAt": created.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "updatedAt": created.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "archived": False,
        })
    return deals


def create_app(deal_count: int, fail_first_429: bool = False) -> Flask:
    app = Flask(__name__)
    deals = build_deals(deal_count)
    state = {"deal_requests": 0}

    def authorized():
        auth = request.headers.get("Authorization", "")
        return auth.startswith("Bearer mock-")

    def rate_headers(resp):
        resp.headers["X-HubSpot-RateLimit-Max"] = "150"
        resp.headers["X-HubSpot-RateLimit-Remaining"] = "149"
        resp.headers["X-HubSpot-RateLimit-Interval-Milliseconds"] = "10000"
        return resp

    def unauthorized():
        return jsonify({
            "status": "error",
            "message": "Authentication credentials not found.",
            "correlationId": "mock-correlation-id",
            "category": "INVALID_AUTHENTICATION",
        }), 401

    @app.get("/crm/v3/objects/deals")
    def list_deals():
        if not authorized():
            return unauthorized()
        state["deal_requests"] += 1
        if fail_first_429 and state["deal_requests"] == 1:
            resp = jsonify({
                "status": "error",
                "message": "You have reached your ten_secondly_rolling limit.",
                "errorType": "RATE_LIMIT",
                "policyName": "TEN_SECONDLY_ROLLING",
            })
            resp.status_code = 429
            resp.headers["X-HubSpot-RateLimit-Interval-Milliseconds"] = "1000"
            return resp
        limit = max(1, min(int(request.args.get("limit", 10)), 100))
        after = int(request.args.get("after", 0) or 0)
        wanted = [p for p in request.args.get("properties", "").split(",") if p]
        page = deals[after:after + limit]
        results = []
        for deal in page:
            props = dict(deal["properties"])
            if wanted:
                props = {name: props.get(name) for name in wanted}
            results.append({**deal, "properties": props})
        body = {"results": results}
        if after + limit < len(deals):
            body["paging"] = {"next": {"after": str(after + limit), "link": "mock"}}
        return rate_headers(jsonify(body))

    @app.get("/crm/v3/properties/deals")
    def list_properties():
        if not authorized():
            return unauthorized()
        names = sorted(deals[0]["properties"].keys()) if deals else []
        return jsonify({"results": [{"name": n, "label": n, "type": "string"} for n in names]})

    @app.get("/crm/v3/pipelines/deals")
    def list_pipelines():
        if not authorized():
            return unauthorized()
        return jsonify({"results": [{
            "id": "default",
            "label": "Sales Pipeline",
            "stages": [{"id": s, "label": s} for s in STAGES],
        }]})

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=5299)
    parser.add_argument("--deals", type=int, default=7)
    parser.add_argument("--fail-first-429", action="store_true")
    args = parser.parse_args()
    create_app(args.deals, args.fail_first_429).run(host="0.0.0.0", port=args.port)
