"""
Contract tests: the HubSpot mock must behave like HubSpot's CRM v3 deals API for
everything the ETL relies on. They run the real HubSpotAPIService against the
mock over HTTP.
"""
import threading

import pytest
import requests
from werkzeug.serving import make_server

from mock_hubspot.server import MESSAGE_401, READ, WRITE, create_app
from services.hubspot_api_service import (
    HubSpotAPIService,
    HubSpotAuthenticationError,
    HubSpotPermissionError,
)

TOKEN = "pat-mock-contract-rw"
READONLY = "pat-mock-contract-ro"
NO_SCOPE = "pat-mock-contract-none"
TOKENS = {TOKEN: ("test", {READ, WRITE}), READONLY: ("test", {READ}), NO_SCOPE: ("test", set())}

DEALS = [
    {"dealname": "Acme Corp - Starter Plan", "amount": "5000", "dealstage": "qualifiedtobuy",
     "dealtype": "newbusiness", "closedate": "2026-12-01T17:00:00Z"},
    {"dealname": "Globex - Annual Subscription", "amount": "25000", "dealstage": "presentationscheduled"},
    {"dealname": "Initech - Enterprise Expansion", "amount": "50000", "dealstage": "closedwon"},
    {"dealname": "Umbrella Health - Platform Migration", "amount": "75000", "dealstage": "closedlost"},
    {"dealname": "Stark Industries - Multi-year Contract", "amount": "100000", "dealstage": "contractsent"},
]


def start(rate_limit=150):
    app = create_app(data_file="", load_deals=0, rate_limit_per_10s=rate_limit, tokens=TOKENS)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def mock():
    server, url = start()
    api = HubSpotAPIService(base_url=url, backoff_seconds=0.01)
    created = [api.create_deal(TOKEN, deal) for deal in DEALS]
    yield url, api, created
    server.shutdown()


def test_unknown_token_gets_hubspot_401_body(mock):
    url, api, _ = mock
    response = requests.get(f"{url}/crm/v3/objects/deals", headers={"Authorization": "Bearer pat-na1-wrong"})
    body = response.json()
    assert response.status_code == 401
    assert body["status"] == "error" and body["category"] == "INVALID_AUTHENTICATION"
    assert body["message"] == MESSAGE_401 and body["correlationId"]
    with pytest.raises(HubSpotAuthenticationError):
        api.get_deals("pat-na1-wrong")


def test_create_returns_201_with_hubspot_calculated_properties(mock):
    url, api, created = mock
    won = created[2]
    assert won["id"].isdigit() and won["archived"] is False
    props = won["properties"]
    assert props["hs_object_id"] == won["id"]
    assert props["hs_is_closed"] == "true" and props["hs_is_closed_won"] == "true"
    assert props["hs_deal_stage_probability"] == "1.0"
    assert props["pipeline"] == "default"
    assert created[0]["properties"]["closedate"] == "2026-12-01T17:00:00Z"


def test_cursor_pagination_matches_hubspot_shape(mock):
    url, api, created = mock
    first = api.get_deals(TOKEN, limit=2)
    assert len(first["results"]) == 2
    assert set(first["paging"]["next"]) == {"after", "link"}
    ids = [d["id"] for d in api.iterate_deals(TOKEN, page_size=2)]
    assert ids == sorted((d["id"] for d in created), key=int)
    last = api.get_deals(TOKEN, limit=2, after=ids[4])
    assert "paging" not in last and len(last["results"]) == 1


def test_default_and_requested_properties(mock):
    url, api, _ = mock
    default = requests.get(f"{url}/crm/v3/objects/deals", params={"limit": 1},
                           headers={"Authorization": f"Bearer {TOKEN}"}).json()["results"][0]["properties"]
    assert set(default) == {"amount", "closedate", "createdate", "dealname", "dealstage",
                            "hs_lastmodifieddate", "hs_object_id", "pipeline"}
    requested = api.get_deals(TOKEN, limit=1, properties=["dealname", "description", "not_a_property"])
    props = requested["results"][0]["properties"]
    assert props["description"] is None                 # defined but empty -> null
    assert "not_a_property" not in props                # unknown -> omitted
    assert {"createdate", "hs_lastmodifieddate", "hs_object_id"} <= set(props)
    assert all(v is None or isinstance(v, str) for v in props.values())  # values are strings


def test_limit_is_capped_at_100_and_validated(mock):
    url, _, _ = mock
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert requests.get(f"{url}/crm/v3/objects/deals", params={"limit": 500}, headers=headers).status_code == 200
    assert requests.get(f"{url}/crm/v3/objects/deals", params={"limit": "x"}, headers=headers).status_code == 400
    assert requests.get(f"{url}/crm/v3/objects/deals", params={"after": "abc"}, headers=headers).status_code == 400


def test_scopes_are_enforced(mock):
    url, api, _ = mock
    with pytest.raises(HubSpotPermissionError):
        api.create_deal(READONLY, {"dealname": "x", "dealstage": "closedwon"})
    with pytest.raises(HubSpotPermissionError):
        api.get_deals(NO_SCOPE)
    assert api.validate_credentials(READONLY)["has_deals_read_scope"] is True
    result = api.validate_credentials(NO_SCOPE)
    assert result["valid"] is True and result["has_deals_read_scope"] is False


def test_rate_limit_headers_and_429_body():
    server, url = start(rate_limit=3)
    try:
        headers = {"Authorization": f"Bearer {TOKEN}"}
        responses = [requests.get(f"{url}/crm/v3/objects/deals", headers=headers) for _ in range(4)]
        assert [r.status_code for r in responses] == [200, 200, 200, 429]
        assert responses[0].headers["X-HubSpot-RateLimit-Max"] == "3"
        assert responses[0].headers["X-HubSpot-RateLimit-Interval-Milliseconds"] == "10000"
        assert responses[2].headers["X-HubSpot-RateLimit-Remaining"] == "0"
        body = responses[3].json()
        assert body["policyName"] == "TEN_SECONDLY_ROLLING" and body["errorType"] == "RATE_LIMIT"
    finally:
        server.shutdown()


def test_archive_and_archived_listing(mock):
    url, api, created = mock
    headers = {"Authorization": f"Bearer {TOKEN}"}
    target = created[3]["id"]
    assert requests.delete(f"{url}/crm/v3/objects/deals/{target}", headers=headers).status_code == 204
    active = [d["id"] for d in api.iterate_deals(TOKEN)]
    archived = api.get_deals(TOKEN, archived=True)["results"]
    assert target not in active and [d["id"] for d in archived] == [target]
    assert archived[0]["archived"] is True and archived[0]["archivedAt"]
    assert requests.get(f"{url}/crm/v3/objects/deals/{target}", headers=headers).status_code == 404


def test_write_validation_errors(mock):
    url, _, _ = mock
    headers = {"Authorization": f"Bearer {TOKEN}"}
    bad = requests.post(f"{url}/crm/v3/objects/deals", headers=headers,
                        json={"properties": {"dealname": "x", "dealstage": "not-a-stage", "nope": "1"}})
    assert bad.status_code == 400 and bad.json()["category"] == "VALIDATION_ERROR"
    assert "PROPERTY_DOESNT_EXIST" in bad.json()["message"] and "INVALID_OPTION" in bad.json()["message"]
    read_only = requests.post(f"{url}/crm/v3/objects/deals", headers=headers,
                              json={"properties": {"hs_is_closed": "true"}})
    assert read_only.status_code == 400 and "READ_ONLY_VALUE" in read_only.json()["message"]


def test_update_and_property_history(mock):
    url, api, created = mock
    headers = {"Authorization": f"Bearer {TOKEN}"}
    deal_id = created[0]["id"]
    updated = requests.patch(f"{url}/crm/v3/objects/deals/{deal_id}", headers=headers,
                             json={"properties": {"dealstage": "closedwon"}}).json()
    assert updated["properties"]["hs_is_closed_won"] == "true"
    history = requests.get(f"{url}/crm/v3/objects/deals/{deal_id}", headers=headers,
                           params={"propertiesWithHistory": "dealstage"}).json()["propertiesWithHistory"]["dealstage"]
    assert [h["value"] for h in history] == ["closedwon", "qualifiedtobuy"]


def test_properties_and_pipelines_endpoints(mock):
    url, api, _ = mock
    properties = {p["name"]: p for p in api.get_deal_properties(TOKEN)}
    assert properties["amount"]["type"] == "number"
    assert properties["closedate"]["type"] == "datetime"
    assert properties["dealstage"]["type"] == "enumeration"
    pipeline = api.get_deal_pipelines(TOKEN)[0]
    assert pipeline["id"] == "default"
    assert [s["id"] for s in pipeline["stages"]][:2] == ["appointmentscheduled", "qualifiedtobuy"]
    assert pipeline["stages"][-1]["metadata"] == {"isClosed": "true", "probability": "0.0"}
