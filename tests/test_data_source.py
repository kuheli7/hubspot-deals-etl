"""Unit tests for the deal transformation and batching logic in services/data_source.py."""
from datetime import datetime, timezone
from decimal import Decimal

from services.data_source import (
    DEAL_COLUMN_HINTS,
    ExtractionProgress,
    create_data_source,
    parse_hubspot_datetime,
    transform_deal,
)


SAMPLE_DEAL = {
    "id": "18374659201",
    "properties": {
        "dealname": "Acme Corp - Annual Plan",
        "amount": "25000.50",
        "dealstage": "presentationscheduled",
        "pipeline": "default",
        "closedate": "2026-11-30T17:00:00.000Z",
        "createdate": "2026-10-01T09:15:22.481Z",
        "hs_lastmodifieddate": "2026-10-05T12:00:00Z",
        "hs_object_id": "18374659201",
        "hs_deal_stage_probability": "0.6",
        "hs_is_closed": "false",
        "days_to_close": "",
        "description": None,
    },
    "createdAt": "2026-10-01T09:15:22.481Z",
    "updatedAt": "2026-10-05T12:00:00Z",
    "archived": False,
}


def test_transform_deal_converts_types_and_adds_metadata():
    extracted_at = datetime(2026, 10, 9, tzinfo=timezone.utc)
    row = transform_deal(SAMPLE_DEAL, "scan-1", "org-1", 3, extracted_at)

    assert row["id"] == "18374659201"
    assert row["amount"] == Decimal("25000.50")
    assert row["closedate"] == datetime(2026, 11, 30, 17, 0, tzinfo=timezone.utc)
    assert row["hs_object_id"] == 18374659201
    assert row["hs_deal_stage_probability"] == 0.6
    assert row["hs_is_closed"] is False
    assert row["days_to_close"] is None          # empty string -> NULL
    assert row["description"] is None
    assert row["dealtype"] is None               # requested but not returned -> NULL
    assert row["created_at"] == datetime(2026, 10, 1, 9, 15, 22, 481000, tzinfo=timezone.utc)
    assert row["_scan_id"] == "scan-1"
    assert row["_tenant_id"] == "org-1"
    assert row["_page_number"] == 3
    assert row["_extracted_at"] == extracted_at
    assert row["_source_service"] == "hubspot_deals"


def test_transform_deal_keeps_extra_properties_as_text():
    deal = {"id": "1", "properties": {"custom_region": "EMEA"}}
    row = transform_deal(deal, "s", "t", 1, requested_properties=["dealname", "custom_region"])
    assert row["custom_region"] == "EMEA"


def test_parse_hubspot_datetime_formats():
    utc = timezone.utc
    assert parse_hubspot_datetime("2026-01-15") == datetime(2026, 1, 15, tzinfo=utc)
    assert parse_hubspot_datetime("1736937000000") == datetime(2025, 1, 15, 10, 30, tzinfo=utc)
    assert parse_hubspot_datetime("2026-01-15T10:30:00Z") == datetime(2026, 1, 15, 10, 30, tzinfo=utc)
    assert parse_hubspot_datetime("") is None
    assert parse_hubspot_datetime("not-a-date") is None


def test_every_metadata_column_has_a_type_hint():
    for column in ("id", "_extracted_at", "_scan_id", "_tenant_id", "_page_number"):
        assert "data_type" in DEAL_COLUMN_HINTS[column]
    assert DEAL_COLUMN_HINTS["amount"]["data_type"] == "decimal"
    # each money column must own its hint dict (dlt mutates hint dicts)
    money = [c for c, h in DEAL_COLUMN_HINTS.items() if h.get("data_type") == "decimal"]
    assert len({id(DEAL_COLUMN_HINTS[c]) for c in money}) == len(money)


class FakeAPI:
    """Serves numbered deals in pages of `page_size`, cursor = next offset"""

    def __init__(self, total):
        self.total = total
        self.cursors = []

    def get_deals(self, access_token, limit, after=None, properties=None, archived=False):
        self.cursors.append(after)
        start = int(after or 0)
        results = [{"id": str(i), "properties": {"amount": "100"}} for i in range(start, min(start + limit, self.total))]
        body = {"results": results}
        if start + limit < self.total:
            body["paging"] = {"next": {"after": str(start + limit)}}
        return body

    @staticmethod
    def get_next_cursor(page):
        return ((page or {}).get("paging") or {}).get("next", {}).get("after")


def run_batch(api, progress, interval=2, **callbacks):
    resources = create_data_source(
        job_config={"organizationId": "org-1", "scanId": "scan-1"},
        auth_config={"accessToken": "pat-na1-test"},
        filters={"scan_id": "scan-1", "pageSize": 2},
        api_service=api,
        progress=progress,
        checkpoint_interval=interval,
        **callbacks,
    )
    rows = []
    for item in resources[0]:
        rows.extend(item if isinstance(item, list) else [item])
    return rows


def test_batches_stop_at_checkpoint_interval_and_resume_from_cursor():
    api = FakeAPI(total=9)
    progress = ExtractionProgress()

    first = run_batch(api, progress)              # pages 1-2
    assert [r["id"] for r in first] == ["0", "1", "2", "3"]
    assert (progress.page_number, progress.cursor, progress.finished) == (2, "4", False)

    # simulate a restart from the committed checkpoint
    checkpoint = {"cursor": progress.cursor, "pageNumber": progress.page_number,
                  "recordsProcessed": progress.records_processed}
    resumed = ExtractionProgress.from_checkpoint(checkpoint)
    rest = []
    while not resumed.finished:
        rest += run_batch(api, resumed)

    assert [r["id"] for r in rest] == ["4", "5", "6", "7", "8"]
    assert resumed.records_processed == 9
    assert resumed.page_number == 5


def test_pause_and_cancel_stop_before_next_page():
    api = FakeAPI(total=10)
    progress = ExtractionProgress()
    rows = run_batch(api, progress, interval=5, check_pause_callback=lambda _id: True)
    assert rows == [] and progress.stop_reason == "paused" and api.cursors == []

    progress = ExtractionProgress()
    rows = run_batch(api, progress, interval=5, check_cancel_callback=lambda _id: True)
    assert rows == [] and progress.stop_reason == "cancelled"


def test_checkpoint_payload_matches_job_service_contract():
    progress = ExtractionProgress(cursor="200", page_number=2, records_processed=200)
    payload = progress.to_checkpoint("deals_batch_committed", page_size=100)
    assert payload["cursor"] == "200"
    assert payload["records_processed"] == 200
    assert payload["page_number"] == 2
    assert payload["batch_size"] == 100

    progress.finished = True
    assert progress.to_checkpoint("deals_completed", 100)["cursor"] is None
