"""Unit tests for services/hubspot_api_service.py (HubSpot is mocked)."""
import json
import time

import pytest
import requests

from services import hubspot_api_service as svc
from services.hubspot_api_service import (
    HubSpotAPIService,
    HubSpotAuthenticationError,
    HubSpotPermissionError,
    HubSpotRateLimitError,
    HubSpotServerError,
    RateLimiter,
)


def make_response(status=200, body=None, headers=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body if body is not None else {}).encode()
    response.headers.update(headers or {})
    response.reason = "mock"
    return response


class FakeSession:
    """Stands in for requests.Session; replays queued responses and records calls"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "headers": headers})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Make retries instant while still recording the requested delays"""
    sleeps = []
    monkeypatch.setattr(svc.time, "sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def client(responses, **kwargs):
    session = FakeSession(responses)
    return HubSpotAPIService(session=session, backoff_seconds=0.01, **kwargs), session


def page(ids, after=None):
    body = {"results": [{"id": str(i), "properties": {"dealname": f"Deal {i}"}} for i in ids]}
    if after:
        body["paging"] = {"next": {"after": after, "link": "x"}}
    return make_response(200, body)


def test_get_deals_sends_bearer_token_and_query_params():
    api, session = client([page([1, 2], after="2")])
    data = api.get_deals("pat-na1-test-token", limit=2, properties=["dealname", "amount"])

    call = session.calls[0]
    assert call["url"] == "https://api.hubapi.com/crm/v3/objects/deals"
    assert call["headers"]["Authorization"] == "Bearer pat-na1-test-token"
    assert call["params"] == {"limit": 2, "properties": "dealname,amount", "archived": "false"}
    assert api.get_next_cursor(data) == "2"


def test_page_size_is_capped_at_hubspot_maximum():
    api, session = client([page([1])])
    api.get_deals("pat-na1-test-token", limit=500)
    assert session.calls[0]["params"]["limit"] == 100


def test_iterate_deals_follows_cursor_until_last_page():
    api, session = client([page([1, 2], after="2"), page([3, 4], after="4"), page([5])])
    ids = [deal["id"] for deal in api.iterate_deals("pat-na1-test-token", page_size=2)]

    assert ids == ["1", "2", "3", "4", "5"]
    assert [c["params"].get("after") for c in session.calls] == [None, "2", "4"]


def test_401_raises_authentication_error_without_retry():
    api, session = client([make_response(401, {"message": "Authentication credentials not found."})])
    with pytest.raises(HubSpotAuthenticationError) as exc:
        api.get_deals("pat-na1-bad-token")
    assert exc.value.status_code == 401
    assert len(session.calls) == 1


def test_403_raises_permission_error_naming_scope():
    api, _ = client([make_response(403, {"message": "missing scopes"})])
    with pytest.raises(HubSpotPermissionError) as exc:
        api.get_deals("pat-na1-test-token")
    assert "crm.objects.deals.read" in exc.value.message


def test_429_ten_secondly_is_retried_after_interval(no_sleep):
    rate_limited = make_response(
        429,
        {"message": "You have reached your ten_secondly_rolling limit.", "policyName": "TEN_SECONDLY_ROLLING"},
        {"X-HubSpot-RateLimit-Interval-Milliseconds": "10000"},
    )
    api, session = client([rate_limited, page([1])])
    data = api.get_deals("pat-na1-test-token")

    assert len(session.calls) == 2
    assert data["results"][0]["id"] == "1"
    assert 10.0 in no_sleep


def test_429_daily_limit_fails_immediately():
    daily = make_response(429, {"message": "You have reached your daily limit.", "policyName": "DAILY"})
    api, session = client([daily, page([1])])
    with pytest.raises(HubSpotRateLimitError):
        api.get_deals("pat-na1-test-token")
    assert len(session.calls) == 1


def test_429_gives_up_after_max_retries():
    responses = [make_response(429, {"message": "slow down"}) for _ in range(4)]
    api, session = client(responses, max_retries=3)
    with pytest.raises(HubSpotRateLimitError):
        api.get_deals("pat-na1-test-token")
    assert len(session.calls) == 4


def test_5xx_and_network_errors_are_retried():
    api, session = client([
        make_response(502, {"message": "bad gateway"}),
        requests.exceptions.ConnectionError("reset"),
        page([7]),
    ])
    data = api.get_deals("pat-na1-test-token")
    assert data["results"][0]["id"] == "7"
    assert len(session.calls) == 3


def test_persistent_5xx_raises_server_error():
    api, _ = client([make_response(503, {"message": "down"}) for _ in range(4)], max_retries=3)
    with pytest.raises(HubSpotServerError):
        api.get_deals("pat-na1-test-token")


def test_missing_token_is_rejected_before_any_request():
    api, session = client([])
    with pytest.raises(HubSpotAuthenticationError):
        api.get_deals("   ")
    assert session.calls == []


def test_validate_credentials_reports_status_without_raising():
    ok_api, _ = client([page([1])])
    assert ok_api.validate_credentials("pat-na1-good")["has_deals_read_scope"] is True

    bad_api, _ = client([make_response(401, {"message": "expired"})])
    result = bad_api.validate_credentials("pat-na1-expired")
    assert result["valid"] is False and result["status_code"] == 401

    scope_api, _ = client([make_response(403, {"message": "scope"})])
    result = scope_api.validate_credentials("pat-na1-noscope")
    assert result["valid"] is True and result["has_deals_read_scope"] is False


def test_rate_limit_headers_are_recorded():
    headers = {"X-HubSpot-RateLimit-Max": "150", "X-HubSpot-RateLimit-Remaining": "120",
               "X-HubSpot-RateLimit-Interval-Milliseconds": "10000"}
    api, _ = client([make_response(200, {"results": []}, headers)])
    api.get_deals("pat-na1-test-token")
    usage = api.get_api_usage()
    assert usage["interval_max"] == "150" and usage["interval_remaining"] == "120"


def test_token_is_masked_for_logs():
    masked = HubSpotAPIService.mask_token("pat-na1-1234567890abcdef")
    assert "1234567890" not in masked and masked.endswith("cdef")


def test_rate_limiter_blocks_when_window_is_full(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(svc.time, "monotonic", lambda: clock["now"])

    def fake_sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(svc.time, "sleep", fake_sleep)
    limiter = RateLimiter(max_requests=3, window_seconds=10)

    waits = [limiter.acquire() for _ in range(4)]

    assert waits[:3] == [0.0, 0.0, 0.0]
    assert waits[3] == pytest.approx(10.0)
