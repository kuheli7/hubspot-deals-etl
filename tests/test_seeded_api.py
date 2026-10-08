"""
Seeded data tests (TEST-GUIDELINES-V1 section 3).

A clean test database is seeded with extraction jobs in every state, plus
extracted deal rows (0, 3 and 250 records). The service API is then exercised
in-process with Flask's test client, with no HubSpot dependency. Every test
reseeds, so tests are independent of each other.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest

from tests.conftest import TEST_DB

psycopg2 = pytest.importorskip("psycopg2")

TENANT = "org-seeded"
OTHER_TENANT = "org-other"
SCHEMA = "hubspot_deals_org_seeded"
OTHER_SCHEMA = "hubspot_deals_org_other"
NOW = datetime.now(timezone.utc).replace(microsecond=0)

# scan id -> (tenant, status, records, start offset s, end offset s or None, error)
SEEDED_JOBS = {
    "seed-pending": (TENANT, "pending", 0, -5, None, None),
    "seed-running": (TENANT, "running", 0, -40, None, None),
    "seed-completed-few": (TENANT, "completed", 3, -120, -60, None),
    "seed-completed-many": (TENANT, "completed", 250, -600, -300, None),
    "seed-completed-zero": (TENANT, "completed", 0, -30, -20, None),
    "seed-cancelled": (TENANT, "cancelled", 2, -90, -80, None),
    "seed-failed": (TENANT, "failed", 0, -70, -69,
                    "HubSpot rejected the access token (401): Authentication credentials not found."),
    "seed-other-tenant": (OTHER_TENANT, "completed", 1, -50, -45, None),
}
ROWS_PER_SCAN = {"seed-completed-few": 3, "seed-completed-many": 250, "seed-cancelled": 2}
API = "/api/v1"


def connect(dbname=None):
    return psycopg2.connect(host=TEST_DB["host"], port=TEST_DB["port"], user=TEST_DB["user"],
                            password=TEST_DB["password"], dbname=dbname or TEST_DB["name"])


@pytest.fixture(scope="session")
def app():
    """Create a clean test database, then the Flask app bound to it"""
    try:
        admin = connect("postgres")
    except psycopg2.OperationalError as exc:
        pytest.skip(f"PostgreSQL not available for seeded tests: {exc}")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f'DROP DATABASE IF EXISTS "{TEST_DB["name"]}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{TEST_DB["name"]}"')
    admin.close()

    import app as app_module  # creates the jobs tables in the test database

    flask_app = app_module.app
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def seeded(request):
    """Reseed the test database before every test that uses the app"""
    if "app" not in request.fixturenames and "client" not in request.fixturenames:
        yield None
        return
    request.getfixturevalue("app")
    conn = connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DELETE FROM job_checkpoints; DELETE FROM jobs;")
        for schema in (SCHEMA, OTHER_SCHEMA):
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'''
                CREATE TABLE "{schema}".deals (
                    id varchar NOT NULL UNIQUE,
                    dealname varchar,
                    amount numeric(18,2),
                    dealstage varchar,
                    closedate timestamptz,
                    _extracted_at timestamptz NOT NULL,
                    _scan_id varchar NOT NULL,
                    _tenant_id varchar NOT NULL,
                    _page_number bigint,
                    _dlt_load_id varchar NOT NULL,
                    _dlt_id varchar NOT NULL UNIQUE
                )''')

        for scan_id, (tenant, status, records, start, end, error) in SEEDED_JOBS.items():
            dataset = SCHEMA if tenant == TENANT else OTHER_SCHEMA
            metadata = {"dataset_name": dataset, "table_name": "deals"} if status != "pending" else {}
            cur.execute(
                """INSERT INTO jobs (id, "organizationId", type, status, "startTime", "endTime",
                                     "lastHeartbeat", "recordsExtracted", "errorMessage", config, job_metadata)
                   VALUES (%s, %s, 'deal', %s, %s, %s, %s, %s, %s, %s, %s)""",
                (scan_id, tenant, status, NOW + timedelta(seconds=start),
                 NOW + timedelta(seconds=end) if end is not None else None,
                 NOW, records, error,
                 '{"auth": "seeded-encrypted-placeholder", "filters": {}, "type": ["deal"]}',
                 psycopg2.extras.Json(metadata)),
            )
            if status in ("completed", "cancelled"):
                cur.execute(
                    """INSERT INTO job_checkpoints (job_id, phase, "recordsProcessed", cursor, "pageNumber", "batchSize")
                       VALUES (%s, %s, %s, NULL, 1, 100)""",
                    (scan_id, "deals_completed" if status == "completed" else "deals_cancelled", records))

        rows = []
        for scan_id, count in ROWS_PER_SCAN.items():
            for i in range(count):
                rows.append((f"{scan_id}-{i:04d}", f"Seeded deal {i} of {scan_id}", 1000 + i, "closedwon",
                             NOW + timedelta(days=i), NOW - timedelta(seconds=i), scan_id, TENANT, i // 100 + 1,
                             "seed-load", f"dlt-{scan_id}-{i}"))
        psycopg2.extras.execute_values(
            cur, f'INSERT INTO "{SCHEMA}".deals VALUES %s', rows)
        cur.execute(f'''INSERT INTO "{OTHER_SCHEMA}".deals VALUES
                        ('other-0001', 'Other tenant deal', 42, 'closedlost', now(), now(),
                         'seed-other-tenant', '{OTHER_TENANT}', 1, 'seed-load', 'dlt-other-1')''')
    conn.close()
    yield SEEDED_JOBS


def rows_for_scan(scan_id):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{SCHEMA}".deals WHERE _scan_id = %s', (scan_id,))
            return cur.fetchone()[0]
    finally:
        conn.close()


import psycopg2.extras  # noqa: E402  (after importorskip)


# ---------------------------------------------------------------------- #
# Section 3, step 6 - health
# ---------------------------------------------------------------------- #
def test_health_endpoints_report_healthy(client):
    service = client.get(f"{API}/health")
    liveness = client.get("/health")
    assert service.status_code == 200 and service.json["status"] == "healthy"
    assert liveness.status_code == 200 and liveness.json["checks"]["database"] == "ok"
    assert service.headers["Content-Type"].startswith("application/json")


# ---------------------------------------------------------------------- #
# Step 2 - job status matches the seeded state
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("scan_id", list(SEEDED_JOBS))
def test_status_matches_seeded_state(client, scan_id):
    tenant, status, records, start, end, error = SEEDED_JOBS[scan_id]
    response = client.get(f"{API}/scan/{scan_id}/status")
    data = response.json["data"]
    assert response.status_code == 200
    assert data["scanId"] == scan_id and data["organizationId"] == tenant
    assert data["status"] == status
    assert data["recordsExtracted"] == records
    assert data["startTime"] is not None
    if end is None:
        assert data["endTime"] is None and "duration" not in data
    else:
        assert data["duration"] == pytest.approx(end - start, abs=1)
    assert data["errorMessage"] == error
    assert data["config"]["auth"] == "***redacted***"


def test_guideline_status_path_alias_returns_same_body(client):
    primary = client.get(f"{API}/scan/seed-completed-few/status").json
    alias = client.get(f"{API}/scan/status/seed-completed-few").json
    assert primary == alias


# ---------------------------------------------------------------------- #
# Step 3 - results match the seeded data
# ---------------------------------------------------------------------- #
def test_results_match_seeded_rows(client):
    response = client.get(f"{API}/scan/result/seed-completed-few")
    data = response.json["data"]
    assert response.status_code == 200
    assert data["pagination"]["total"] == 3
    ids = {r["id"] for r in data["records"]}
    assert ids == {f"seed-completed-few-{i:04d}" for i in range(3)}
    first = next(r for r in data["records"] if r["id"] == "seed-completed-few-0000")
    assert first["dealname"] == "Seeded deal 0 of seed-completed-few"
    assert first["amount"] == 1000.0
    assert first["_scan_id"] == "seed-completed-few" and first["_tenant_id"] == TENANT
    # only rows of this scan, never the other scans in the same tenant schema
    assert all(r["_scan_id"] == "seed-completed-few" for r in data["records"])


def test_results_pagination_over_many_records(client):
    pages, seen = [], []
    for offset in (0, 100, 200):
        data = client.get(f"{API}/results/seed-completed-many/result?limit=100&offset={offset}").json["data"]
        pages.append(data["pagination"])
        seen += [r["id"] for r in data["records"]]
    assert [p["hasMore"] for p in pages] == [True, True, False]
    assert all(p["total"] == 250 and p["totalPages"] == 3 for p in pages)
    assert len(seen) == 250 and len(set(seen)) == 250


def test_results_for_completed_scan_with_zero_records(client):
    response = client.get(f"{API}/results/seed-completed-zero/result")
    assert response.status_code == 200
    assert response.json["data"]["records"] == []
    assert response.json["data"]["pagination"]["total"] == 0


@pytest.mark.parametrize("scan_id", ["seed-pending", "seed-running"])
def test_results_for_incomplete_scan_conflict(client, scan_id):
    response = client.get(f"{API}/results/{scan_id}/result")
    assert response.status_code == 409
    assert "not completed" in response.json["message"].lower()


def test_results_limit_validation(client):
    assert client.get(f"{API}/results/seed-completed-many/result?limit=0").status_code == 400
    assert client.get(f"{API}/results/seed-completed-many/result?limit=501").status_code == 400
    assert client.get(f"{API}/results/seed-completed-many/result?offset=-1").status_code == 400


# ---------------------------------------------------------------------- #
# Step 4 - list all jobs, pagination and filtering
# ---------------------------------------------------------------------- #
def test_list_jobs_includes_every_seeded_job(client):
    data = client.get(f"{API}/jobs/jobs?organizationId={TENANT}&limit=100").json["data"]
    listed = {s["scanId"]: s["status"] for s in data["scans"]}
    expected = {k: v[1] for k, v in SEEDED_JOBS.items() if v[0] == TENANT}
    assert listed == expected
    assert all(s["config"]["auth"] == "***redacted***" for s in data["scans"])


def test_list_jobs_pagination_and_tenant_filter(client):
    first = client.get(f"{API}/scan/list?organizationId={TENANT}&limit=3&offset=0").json["data"]
    second = client.get(f"{API}/scan/list?organizationId={TENANT}&limit=3&offset=3").json["data"]
    assert first["pagination"]["returned"] == 3 and first["pagination"]["hasMore"] is True
    assert not {s["scanId"] for s in first["scans"]} & {s["scanId"] for s in second["scans"]}
    other = client.get(f"{API}/jobs/jobs?organizationId={OTHER_TENANT}").json["data"]["scans"]
    assert [s["scanId"] for s in other] == ["seed-other-tenant"]
    assert client.get(f"{API}/jobs/jobs?limit=101").status_code == 400


# ---------------------------------------------------------------------- #
# Step 5 - statistics reflect the seeded data
# ---------------------------------------------------------------------- #
def test_statistics_reflect_seeded_jobs(client):
    stats = client.get(f"{API}/jobs/statistics?organizationId={TENANT}").json["data"]
    tenant_jobs = [v for v in SEEDED_JOBS.values() if v[0] == TENANT]
    assert stats["total_jobs"] == len(tenant_jobs)
    for status in ("pending", "running", "completed", "cancelled", "failed"):
        assert stats["status_breakdown"][status] == sum(1 for v in tenant_jobs if v[1] == status)
    assert stats["total_records_extracted"] == sum(v[2] for v in tenant_jobs)
    durations = [v[4] - v[3] for v in tenant_jobs if v[1] == "completed"]
    assert stats["extraction_time"]["average_seconds"] == pytest.approx(sum(durations) / len(durations), abs=1)
    assert stats["extraction_time"]["completed_jobs_measured"] == len(durations)

    everything = client.get(f"{API}/jobs/statistics").json["data"]
    assert everything["total_jobs"] == len(SEEDED_JOBS)


# ---------------------------------------------------------------------- #
# Step 7 - cancel a pending job; cancel in a final state is a conflict
# ---------------------------------------------------------------------- #
def test_cancel_pending_job(client):
    response = client.post(f"{API}/scan/cancel/seed-pending")
    assert response.status_code == 200
    assert response.json["success"] is True and response.json["status"] == "cancelled"
    assert "cancelled successfully" in response.json["message"]
    assert client.get(f"{API}/scan/seed-pending/status").json["data"]["status"] == "cancelled"


@pytest.mark.parametrize("scan_id", ["seed-completed-few", "seed-failed", "seed-cancelled"])
def test_cancel_job_in_final_state_conflicts(client, scan_id):
    response = client.post(f"{API}/scan/{scan_id}/cancel")
    assert response.status_code == 409
    assert "cannot cancel" in response.json["message"].lower()


# ---------------------------------------------------------------------- #
# Step 8 - remove job data, then status and results are gone
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("scan_id", ["seed-completed-few", "seed-cancelled"])
def test_remove_job_and_its_data(client, scan_id):
    other_rows_before = rows_for_scan("seed-completed-many")
    response = client.delete(f"{API}/scan/remove/{scan_id}")
    assert response.status_code == 200 and response.json["success"] is True
    assert "successfully removed" in response.json["message"]
    assert client.get(f"{API}/scan/{scan_id}/status").status_code == 404
    assert client.get(f"{API}/results/{scan_id}/result").status_code == 404
    assert rows_for_scan(scan_id) == 0
    assert rows_for_scan("seed-completed-many") == other_rows_before  # other scans untouched


def test_remove_running_job_is_rejected(client):
    response = client.delete(f"{API}/scan/seed-running/remove")
    assert response.status_code == 400
    assert client.get(f"{API}/scan/seed-running/status").status_code == 200


# ---------------------------------------------------------------------- #
# Edge cases (section 7.2) that need no HubSpot call
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("method,path", [
    ("get", "/scan/{id}/status"), ("get", "/scan/status/{id}"),
    ("get", "/results/{id}/result"), ("get", "/scan/result/{id}"),
    ("post", "/scan/{id}/cancel"), ("post", "/scan/cancel/{id}"),
    ("delete", "/scan/{id}/remove"), ("delete", "/scan/remove/{id}"),
])
def test_unknown_job_id_returns_404(client, method, path):
    response = getattr(client, method)(API + path.format(id="does-not-exist"))
    assert response.status_code == 404
    assert "does-not-exist" in response.json["message"] or "not found" in response.json["message"].lower()


@pytest.mark.parametrize("job_id", [
    "' OR '1'='1", "seed-completed-few'; DROP TABLE jobs;--", "../../etc/passwd",
    "x" * 5000, "%00", "<script>alert(1)</script>", '{"$ne": null}',
])
def test_injection_and_oversized_ids_are_harmless(client, job_id):
    encoded = quote(job_id, safe="")
    for method, path in (("get", f"/scan/{encoded}/status"), ("get", f"/results/{encoded}/result"),
                         ("post", f"/scan/{encoded}/cancel"), ("delete", f"/scan/{encoded}/remove")):
        response = getattr(client, method)(API + path)
        assert response.status_code in (400, 404), (method, path, response.status_code)
    # nothing was modified
    data = client.get(f"{API}/jobs/jobs?limit=100").json["data"]
    assert len(data["scans"]) == len(SEEDED_JOBS)
    assert rows_for_scan("seed-completed-few") == 3


@pytest.mark.parametrize("body,expected_field", [
    ({"config": {"organizationId": TENANT, "type": ["deal"], "auth": {"accessToken": "pat-na1-0123456789"}}}, "scanId"),
    ({"config": {"scanId": "s1", "organizationId": TENANT, "type": ["deal"]}}, "auth"),
    ({"config": {"scanId": "s1", "organizationId": TENANT, "type": ["deal"], "auth": {"accessToken": ""}}}, "auth"),
    ({"config": {"scanId": "s1", "organizationId": TENANT, "type": ["user"], "auth": {"accessToken": "pat-na1-0123456789"}}}, "type"),
    ({"config": {"scanId": "bad id!", "organizationId": TENANT, "type": ["deal"], "auth": {"accessToken": "pat-na1-0123456789"}}}, "scanId"),
    ({"config": {"scanId": "s1", "organizationId": "org'; DROP TABLE jobs;--", "type": ["deal"], "auth": {"accessToken": "pat-na1-0123456789"}}}, "organizationId"),
    ({"config": {"scanId": "s1", "organizationId": TENANT, "type": ["deal"], "auth": {"accessToken": "pat-na1-0123456789"}, "filters": {"pageSize": 500}}}, "filters"),
])
def test_start_rejects_invalid_bodies_with_field_errors(client, body, expected_field):
    response = client.post(f"{API}/scan/start", json=body)
    assert response.status_code == 400
    assert expected_field in response.json["validation_errors"]["config"]


def test_start_rejects_malformed_json(client):
    response = client.post(f"{API}/scan/start", data='{"config": ', content_type="application/json")
    assert response.status_code == 400
    assert response.json["message"] == "Request body must be a valid JSON object"


def test_start_with_existing_scan_id_conflicts(client):
    body = {"config": {"scanId": "seed-completed-few", "organizationId": TENANT, "type": ["deal"],
                       "auth": {"accessToken": "pat-na1-0123456789"}}}
    response = client.post(f"{API}/scan/start", json=body)
    assert response.status_code == 409
    assert "already exists" in response.json["message"]
    # the seeded job is untouched
    assert client.get(f"{API}/scan/seed-completed-few/status").json["data"]["status"] == "completed"
