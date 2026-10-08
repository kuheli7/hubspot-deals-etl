"""
End-to-end validation of the running service against a real HubSpot test account.

Prerequisites
  1. docker-compose up -d --build            (service on http://localhost:5200)
  2. HUBSPOT_ACCESS_TOKEN=pat-... in .env    (never committed)
  3. python scripts/create_test_deals.py     (records the 5 deal IDs)

    python scripts/run_extraction_test.py [--restart-test]

Every result written to test-results/ comes from this run; tokens are scrubbed
from all saved output.
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from scripts.common import (  # noqa: E402
    PROJECT_ROOT, TEST_RESULTS, get_access_token, load_env, scrub, write_json, write_text,
)

SERVICE = "http://localhost:5200"
API = f"{SERVICE}/api/v1"
TENANT = "org-hubspot-test"
CHECKPOINT_TENANT = "org-hubspot-ckpt"
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
TERMINAL = {"completed", "failed", "cancelled"}

report: Dict[str, Any] = {"run_id": RUN_ID, "started_at": datetime.now(timezone.utc).isoformat(), "checks": []}


def check(name: str, passed: bool, detail: Any = None) -> bool:
    report["checks"].append({"name": name, "passed": bool(passed), "detail": detail})
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" - {detail}" if detail not in (None, "") else ""))
    return passed


def call(method: str, path: str, **kwargs) -> requests.Response:
    url = path if path.startswith("http") else f"{API}{path}"
    return requests.request(method, url, timeout=60, **kwargs)


def scan_body(scan_id: str, tenant: str, token: str, filters: Optional[Dict] = None) -> Dict:
    return {"config": {"scanId": scan_id, "organizationId": tenant, "type": ["deal"],
                       "auth": {"accessToken": token}, "filters": filters or {}}}


def status_of(scan_id: str) -> Dict[str, Any]:
    response = call("GET", f"/scan/{scan_id}/status")
    return response.json().get("data", {}) if response.ok else {}


def wait_for(scan_id: str, predicate: Callable[[Dict], bool], timeout: float = 300, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    timeline: List[str] = []
    data: Dict[str, Any] = {}
    while time.monotonic() < deadline:
        data = status_of(scan_id)
        state = data.get("status")
        if state and (not timeline or timeline[-1] != state):
            timeline.append(state)
        if predicate(data):
            return data, timeline
        time.sleep(interval)
    return data, timeline


def latest_checkpoint(data: Dict) -> Dict:
    return ((data.get("checkpointInfo") or {}).get("latestCheckpoint")) or {}


def db_connect(env):
    import psycopg2
    return psycopg2.connect(host="localhost", port=int(env.get("DB_PORT", 5432)),
                            dbname=env.get("DB_NAME", "hubspot_deals_data_dev"),
                            user=env.get("DB_USER", "postgres"), password=env.get("DB_PASSWORD", "password123"))


def query(conn, sql: str, params=None) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


# ---------------------------------------------------------------------- #
def test_health() -> None:
    started = time.monotonic()
    liveness = call("GET", f"{SERVICE}/health")
    liveness_ms = round((time.monotonic() - started) * 1000, 1)
    service_health = call("GET", "/health")
    docs = call("GET", f"{SERVICE}/docs/")
    swagger = call("GET", "/swagger.json")
    paths = sorted(swagger.json().get("paths", {}).keys()) if swagger.ok else []

    write_json("health_check.json", {
        "GET /health": {"status_code": liveness.status_code, "response_ms": liveness_ms, "body": liveness.json()},
        "GET /api/v1/health": {"status_code": service_health.status_code, "body": service_health.json()},
        "GET /docs/": {"status_code": docs.status_code, "content_type": docs.headers.get("Content-Type")},
        "GET /api/v1/swagger.json": {"status_code": swagger.status_code, "documented_paths": paths},
    })
    check("GET /health returns 200 healthy", liveness.ok and liveness.json().get("status") == "healthy",
          f"{liveness_ms} ms")
    check("GET /api/v1/health returns healthy", service_health.ok and service_health.json().get("status") == "healthy")
    check("Swagger UI served at /docs/", docs.status_code == 200 and "html" in docs.headers.get("Content-Type", ""))
    check("OpenAPI spec lists the deal endpoints", "/scan/start" in paths and "/auth/validate" in paths,
          f"{len(paths)} paths")


def test_credentials(token: str) -> bool:
    response = call("POST", "/auth/validate", json={"accessToken": token})
    data = response.json().get("data", {})
    write_json("credential_validation.json", {"status_code": response.status_code, "response": response.json()})
    return check("Access token valid with crm.objects.deals.read", response.status_code == 200 and data.get("has_deals_read_scope"),
                 f"rate limit headers: {data.get('rate_limit')}")


def test_full_extraction(token: str, env, expected: List[Dict]) -> Optional[str]:
    scan_id = f"deals-e2e-{RUN_ID}"
    started = time.monotonic()
    response = call("POST", "/scan/start", json=scan_body(scan_id, TENANT, token))
    check("POST /scan/start accepted", response.status_code == 202, response.status_code)

    final, timeline = wait_for(scan_id, lambda d: d.get("status") in TERMINAL)
    duration = round(time.monotonic() - started, 2)
    check("Scan completed", final.get("status") == "completed",
          f"timeline={' -> '.join(timeline)}, {duration}s, error={final.get('errorMessage')}")

    results = call("GET", f"/results/{scan_id}/result", params={"tableName": "deals", "limit": 100})
    records = results.json().get("data", {}).get("records", []) if results.ok else []
    tables = call("GET", f"/results/{scan_id}/tables")

    write_json("extraction_results.json", {
        "scan_id": scan_id,
        "tenant": TENANT,
        "duration_seconds": duration,
        "status_timeline": timeline,
        "final_status": final,
        "tables": tables.json() if tables.ok else tables.text,
        "results_pagination": results.json().get("data", {}).get("pagination") if results.ok else None,
        "records": records,
    })

    by_id = {r["id"]: r for r in records}
    rows = []
    all_match = True
    for deal in expected:
        got = by_id.get(deal["id"])
        checks = {
            "present": got is not None,
            "dealname": bool(got) and got.get("dealname") == deal["dealname"],
            "amount": bool(got) and got.get("amount") is not None and Decimal(str(got["amount"])) == Decimal(deal["amount"]),
            "dealstage": bool(got) and got.get("dealstage") == deal["dealstage"],
            "dealtype": bool(got) and got.get("dealtype") == deal["dealtype"],
            "closedate": bool(got) and str(got.get("closedate", ""))[:10] == deal["closedate"][:10],
            "tenant": bool(got) and got.get("_tenant_id") == TENANT,
            "scan": bool(got) and got.get("_scan_id") == scan_id,
        }
        all_match &= all(checks.values())
        rows.append({"id": deal["id"], "dealname": deal["dealname"], **checks})
    write_json("deal_verification.json", {"scan_id": scan_id, "expected_deals": len(expected),
                                          "records_returned": len(records), "per_deal": rows})
    found = sum(r["present"] for r in rows)
    check(f"All {len(expected)} test deals extracted", found == len(expected), f"{found}/{len(expected)} found")
    check("Extracted fields match HubSpot values", all_match)
    check("recordsExtracted equals HubSpot deal count", final.get("recordsExtracted") == len(records),
          f"recordsExtracted={final.get('recordsExtracted')}, rows={len(records)}")
    check("API response time acceptable (< 120 s end-to-end)", duration < 120, f"{duration}s")
    return scan_id


def test_database(env, scan_id: str, expected: List[Dict]) -> None:
    schema = "hubspot_deals_" + TENANT.replace("-", "_")
    conn = db_connect(env)
    try:
        columns = query(conn, """
            SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable
            FROM information_schema.columns WHERE table_schema = %s AND table_name = 'deals'
            ORDER BY ordinal_position""", (schema,))
        indexes = query(conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = 'deals' ORDER BY indexname", (schema,))
        rows = query(conn, f"""
            SELECT id, dealname, amount, dealstage, pipeline, dealtype, closedate, createdate,
                   hs_priority, hs_is_closed, hs_is_closed_won, _tenant_id, _scan_id, _extracted_at, _page_number
            FROM "{schema}".deals WHERE _scan_id = %s ORDER BY amount""", (scan_id,))
        dupes = query(conn, f'SELECT id, count(*) FROM "{schema}".deals GROUP BY id HAVING count(*) > 1')
        stages = query(conn, f'SELECT dealstage, count(*) AS deals, sum(amount) AS total_amount FROM "{schema}".deals WHERE _scan_id = %s GROUP BY dealstage ORDER BY dealstage', (scan_id,))
        jobs = query(conn, """SELECT id, "organizationId", status, "recordsExtracted", "startTime", "endTime" FROM jobs WHERE id = %s""", (scan_id,))
        checkpoints = query(conn, """SELECT phase, "pageNumber", "recordsProcessed", cursor, "createdAt" FROM job_checkpoints WHERE job_id = %s ORDER BY id""", (scan_id,))
    finally:
        conn.close()

    types = {c["column_name"]: c for c in columns}
    write_json("database_verification.json", {
        "schema": schema, "table": "deals", "columns": columns, "indexes": indexes,
        "rows_for_scan": rows, "duplicate_ids": dupes, "deals_by_stage": stages,
        "job_record": jobs, "checkpoints": checkpoints,
    })
    lines = [f"Schema: {schema}.deals", "", "| column | type | nullable |", "|---|---|---|"]
    for c in columns:
        t = c["data_type"] + (f"({c['numeric_precision']},{c['numeric_scale']})" if c["data_type"] == "numeric" else "")
        lines.append(f"| {c['column_name']} | {t} | {c['is_nullable']} |")
    lines += ["", "Indexes:"] + [f"- `{i['indexdef']}`" for i in indexes]
    lines += ["", "Rows loaded by this scan:", "", "| id | dealname | amount | dealstage | closedate | _tenant_id |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['dealname']} | {r['amount']} | {r['dealstage']} | {r['closedate']} | {r['_tenant_id']} |" for r in rows]
    write_text("database_verification.md", "\n".join(lines) + "\n")

    check("deals table holds one row per test deal", len(rows) == len(expected), f"{len(rows)} rows")
    check("No duplicate deal IDs", not dupes)
    check("amount stored as numeric(18,2)", types.get("amount", {}).get("data_type") == "numeric"
          and types["amount"].get("numeric_scale") == 2)
    check("closedate stored as timestamptz", types.get("closedate", {}).get("data_type") == "timestamp with time zone")
    check("ETL metadata columns present", {"_extracted_at", "_scan_id", "_tenant_id"} <= set(types))
    names = {i["indexname"] for i in indexes}
    check("Tenant/date/stage indexes created",
          {"idx_deals_tenant", "idx_deals_tenant_stage", "idx_deals_tenant_closedate"} <= names, sorted(names))


def test_checkpoint_resume(token: str, env, expected_count: int) -> None:
    """Pause mid-scan, confirm committed checkpoint + partial load, resume, confirm completion"""
    scan_id = f"deals-checkpoint-{RUN_ID}"
    schema = "hubspot_deals_" + CHECKPOINT_TENANT.replace("-", "_")
    call("POST", "/scan/start", json=scan_body(scan_id, CHECKPOINT_TENANT, token,
                                               {"pageSize": 1, "checkpointInterval": 1}))
    at_checkpoint, _ = wait_for(scan_id, lambda d: latest_checkpoint(d).get("phase") == "deals_batch_committed"
                                or d.get("status") in TERMINAL, timeout=120, interval=0.2)
    pause = call("POST", f"/scan/{scan_id}/pause")
    paused, timeline = wait_for(scan_id, lambda d: d.get("status") in TERMINAL
                                or latest_checkpoint(d).get("phase") == "deals_paused", timeout=120)
    paused_ckpt = latest_checkpoint(paused)

    rows_while_paused = None
    results_while_paused = call("GET", f"/results/{scan_id}/result")
    conn = db_connect(env)
    try:
        rows_while_paused = query(conn, f'SELECT count(*) AS n FROM "{schema}".deals WHERE _scan_id = %s', (scan_id,))[0]["n"]
    except Exception:
        conn.rollback()
        rows_while_paused = 0

    resume = call("POST", f"/scan/{scan_id}/resume")
    final, timeline2 = wait_for(scan_id, lambda d: d.get("status") in TERMINAL)
    try:
        final_rows = query(conn, f'SELECT count(*) AS n, count(DISTINCT id) AS distinct_ids FROM "{schema}".deals WHERE _scan_id = %s', (scan_id,))[0]
        checkpoints = query(conn, """SELECT phase, "pageNumber", "recordsProcessed", cursor, "createdAt" FROM job_checkpoints WHERE job_id = %s ORDER BY id""", (scan_id,))
    finally:
        conn.close()

    write_json("checkpoint_test.json", {
        "scan_id": scan_id, "tenant": CHECKPOINT_TENANT,
        "filters": {"pageSize": 1, "checkpointInterval": 1},
        "first_committed_checkpoint": latest_checkpoint(at_checkpoint),
        "pause_response": {"status_code": pause.status_code, "body": pause.json()},
        "paused_status": paused.get("status"), "paused_checkpoint": paused_ckpt,
        "rows_loaded_while_paused": rows_while_paused,
        "results_while_paused": {"status_code": results_while_paused.status_code, "body": results_while_paused.json()},
        "resume_response": {"status_code": resume.status_code, "body": resume.json()},
        "final_status": final.get("status"), "final_records_extracted": final.get("recordsExtracted"),
        "final_rows": final_rows, "checkpoints": checkpoints,
        "status_timeline": timeline + timeline2,
    })
    paused_ok = paused.get("status") == "paused" and paused_ckpt.get("cursor")
    check("Scan paused mid-run with committed checkpoint cursor", paused_ok,
          f"paused at page {paused_ckpt.get('pageNumber')} with {rows_while_paused} rows loaded")
    check("Results blocked (409) while scan is paused", results_while_paused.status_code == 409)
    check("Resume accepted (202)", resume.status_code == 202, resume.json().get("message"))
    check("Resumed scan completed with all deals and no duplicates",
          final.get("status") == "completed" and final_rows["n"] == expected_count == final_rows["distinct_ids"],
          f"{final_rows['n']} rows, {final_rows['distinct_ids']} distinct, recordsExtracted={final.get('recordsExtracted')}")
    check("Resume continued from checkpoint instead of restarting",
          bool(checkpoints) and any(c["phase"] == "deals_paused" for c in checkpoints)
          and checkpoints[-1]["recordsProcessed"] == expected_count,
          [f"{c['phase']}@{c['pageNumber']}" for c in checkpoints])


def test_edge_cases(token: str, completed_scan: str) -> None:
    cases = []

    def case(name, response, expected):
        ok = response.status_code in expected
        cases.append({"case": name, "expected": expected, "actual": response.status_code,
                      "passed": ok, "body": response.json() if response.headers.get("Content-Type", "").startswith("application/json") else response.text[:200]})
        check(f"Edge case: {name}", ok, f"HTTP {response.status_code}")

    case("validate invalid token -> 401", call("POST", "/auth/validate", json={"accessToken": "pat-na1-00000000-invalid-token"}), [401])
    bad_scan = f"deals-badtoken-{RUN_ID}"
    call("POST", "/scan/start", json=scan_body(bad_scan, TENANT, "pat-na1-00000000-invalid-token"))
    failed, _ = wait_for(bad_scan, lambda d: d.get("status") in TERMINAL, timeout=60)
    ok = failed.get("status") == "failed" and "401" in (failed.get("errorMessage") or "")
    cases.append({"case": "scan with invalid token ends failed with 401 detail", "passed": ok,
                  "actual": failed.get("status"), "error": failed.get("errorMessage")})
    check("Edge case: scan with invalid token fails with clear 401 error", ok, failed.get("errorMessage"))
    case("missing token -> 400", call("POST", "/scan/start", json={"config": {"scanId": "x", "organizationId": "o", "type": ["deal"], "auth": {}}}), [400])
    case("malformed JSON -> 400", call("POST", "/scan/start", data="{\"config\": ", headers={"Content-Type": "application/json"}), [400])
    wrong_type = scan_body("x-type", TENANT, token)
    wrong_type["config"]["type"] = ["user"]
    case("unsupported type -> 400", call("POST", "/scan/start", json=wrong_type), [400])
    case("SQL injection in organizationId -> 400", call("POST", "/scan/start", json=scan_body("x-inj", "org'; DROP TABLE jobs;--", token)), [400])
    case("duplicate scanId -> 409", call("POST", "/scan/start", json=scan_body(completed_scan, TENANT, token)), [409])
    case("status of unknown scan -> 404", call("GET", "/scan/no-such-scan/status"), [404])
    case("results of unknown scan -> 404", call("GET", "/results/no-such-scan/result"), [404])
    case("cancel unknown scan -> 404", call("POST", "/scan/no-such-scan/cancel"), [404])
    case("remove unknown scan -> 404", call("DELETE", "/scan/no-such-scan/remove"), [404])
    case("cancel completed scan -> 409", call("POST", f"/scan/{completed_scan}/cancel"), [409])
    case("results limit above maximum -> 400", call("GET", f"/results/{completed_scan}/result", params={"limit": 5000}), [400])
    write_json("edge_cases.json", {"cases": cases})


def test_restart(scan_id: str) -> None:
    started = time.monotonic()
    proc = subprocess.run(["docker", "compose", "restart", "hubspot_deals_service_dev"],
                          cwd=PROJECT_ROOT, capture_output=True, text=True)
    healthy = False
    while time.monotonic() - started < 120:
        try:
            if requests.get(f"{SERVICE}/health", timeout=3).ok:
                healthy = True
                break
        except requests.RequestException:
            pass
        time.sleep(2)
    downtime = round(time.monotonic() - started, 1)
    after = status_of(scan_id)
    results = call("GET", f"/results/{scan_id}/result")
    write_json("restart_test.json", {"restart_exit_code": proc.returncode, "seconds_until_healthy": downtime,
                                     "scan_status_after_restart": after.get("status"),
                                     "results_after_restart": results.status_code})
    check("Service restarts cleanly and is healthy again", proc.returncode == 0 and healthy, f"{downtime}s")
    check("Scans and results survive restart", after.get("status") == "completed" and results.ok)


def save_environment_snapshot() -> None:
    ps = subprocess.run(["docker", "compose", "ps"], cwd=PROJECT_ROOT, capture_output=True, text=True)
    write_text("docker_compose_ps.txt", ps.stdout)
    logs = subprocess.run(["docker", "compose", "logs", "--no-color", "--since", "20m", "hubspot_deals_service_dev"],
                          cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    keep = [line for line in logs.stdout.splitlines()
            if '"level": "DEBUG"' not in line and "sqlalchemy" not in line and "INFO sqlalchemy" not in line]
    (TEST_RESULTS / "logs").mkdir(parents=True, exist_ok=True)
    write_text("logs/service_test_run.log", "\n".join(keep[-1500:]) + "\n")


def write_report() -> None:
    passed = sum(c["passed"] for c in report["checks"])
    total = len(report["checks"])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = {"passed": passed, "failed": total - passed, "total": total}
    write_json("test_run_summary.json", report)
    lines = [f"# Extraction test run {RUN_ID}", "",
             f"Started {report['started_at']} - finished {report['finished_at']}", "",
             f"**{passed}/{total} checks passed**", "", "| Result | Check | Detail |", "|---|---|---|"]
    for c in report["checks"]:
        detail = str(scrub(c["detail"])).replace("|", "\\|") if c["detail"] is not None else ""
        lines.append(f"| {'PASS' if c['passed'] else 'FAIL'} | {c['name']} | {detail} |")
    write_text("test_run_summary.md", "\n".join(lines) + "\n")
    print(f"\n{passed}/{total} checks passed - see test-results/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--restart-test", action="store_true", help="also restart the service container")
    args = parser.parse_args()

    env = load_env()
    token = get_access_token(env)
    created = TEST_RESULTS / "test_deals_created.json"
    if not created.exists():
        raise SystemExit("Run scripts/create_test_deals.py first (needs test-results/test_deals_created.json)")
    expected = __import__("json").loads(created.read_text(encoding="utf-8"))["deals"]

    test_health()
    if not test_credentials(token):
        write_report()
        return 1
    scan_id = test_full_extraction(token, env, expected)
    test_database(env, scan_id, expected)
    test_checkpoint_resume(token, env, len(expected))
    test_edge_cases(token, scan_id)
    if args.restart_test:
        test_restart(scan_id)
    save_environment_snapshot()
    write_report()
    return 0 if all(c["passed"] for c in report["checks"]) else 1


if __name__ == "__main__":
    sys.exit(main())
