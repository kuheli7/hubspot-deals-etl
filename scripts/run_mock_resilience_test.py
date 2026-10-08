"""
Resilience tests that need control over HubSpot's behaviour, run against the
local HubSpot mock (mock_hubspot/): large-volume pagination, rate limiting,
outages, the daily limit, missing scopes and archived deals.

Prerequisites: the stack runs with docker-compose.mock.yml (see README).

    python scripts/run_mock_resilience_test.py

Results: test-results/resilience_test.json and resilience_test.md
"""
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from scripts.common import load_env, write_json, write_text  # noqa: E402
from scripts.run_extraction_test import (  # noqa: E402
    TERMINAL, call, db_connect, latest_checkpoint, query, scan_body, status_of, wait_for,
)

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
checks: List[Dict[str, Any]] = []
details: Dict[str, Any] = {"run_id": RUN_ID}


def check(name: str, passed: bool, detail: Any = None) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" - {detail}" if detail not in (None, "") else ""))


class Mock:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def stats(self) -> Dict[str, Any]:
        return requests.get(f"{self.base}/__mock/stats", timeout=10).json()

    def config(self, **settings) -> None:
        requests.put(f"{self.base}/__mock/config", json=settings, timeout=10).raise_for_status()

    def fault(self, **fault) -> None:
        requests.post(f"{self.base}/__mock/faults", json=fault, timeout=10).raise_for_status()

    def clear_faults(self) -> None:
        requests.delete(f"{self.base}/__mock/faults", timeout=10)

    def hubspot(self, method: str, path: str, token: str, **kwargs):
        return requests.request(method, f"{self.base}{path}", headers={"Authorization": f"Bearer {token}"},
                                timeout=30, **kwargs)


def run_scan(scan_id: str, tenant: str, token: str, filters=None, timeout=600):
    started = time.monotonic()
    response = call("POST", "/scan/start", json=scan_body(scan_id, tenant, token, filters))
    final, timeline = wait_for(scan_id, lambda d: d.get("status") in TERMINAL, timeout=timeout, interval=1)
    return response.status_code, final, timeline, round(time.monotonic() - started, 1)


def rows_for(env, tenant: str, scan_id: str) -> Dict[str, int]:
    schema = "hubspot_deals_" + tenant.replace("-", "_")
    conn = db_connect(env)
    try:
        return query(conn, f'SELECT count(*) AS n, count(DISTINCT id) AS distinct_ids FROM "{schema}".deals '
                           f'WHERE _scan_id = %s', (scan_id,))[0]
    except Exception:
        return {"n": 0, "distinct_ids": 0}
    finally:
        conn.close()


def main() -> int:
    env = load_env()
    mock = Mock(env.get("HUBSPOT_MOCK_URL", "http://localhost:5299"))
    test_token = env.get("HUBSPOT_ACCESS_TOKEN", "pat-mock-test-account-deals")
    load_token = env.get("MOCK_HUBSPOT_LOAD_TOKEN", "pat-mock-load-account-deals")
    noscope_token = env.get("MOCK_HUBSPOT_NO_SCOPE_TOKEN", "pat-mock-test-account-noscope")

    accounts = mock.stats()["accounts"]
    load_total = accounts["load"]["deals"] - accounts["load"]["archived"]
    test_total = accounts["test"]["deals"] - accounts["test"]["archived"]
    details["mock_accounts_before"] = accounts
    mock.clear_faults()
    mock.config(rate_limit_per_10s=150, latency_ms=0, reset_usage=True)

    # 1. Large volume: 2,500 deals -> 25 pages, 3 checkpointed batches
    scan = f"load-volume-{RUN_ID}"
    code, final, timeline, seconds = run_scan(scan, "org-load-test", load_token)
    rows = rows_for(env, "org-load-test", scan)
    summary = (final.get("metadata") or {}).get("extraction_summary", {})
    pages, offset, seen = 0, 0, set()
    while True:
        page = call("GET", f"/results/{scan}/result", params={"limit": 500, "offset": offset}).json()["data"]
        pages += 1
        seen.update(r["id"] for r in page["records"])
        if not page["pagination"]["hasMore"]:
            break
        offset += 500
    details["large_volume"] = {"scan_id": scan, "seconds": seconds, "final_status": final.get("status"),
                               "records_extracted": final.get("recordsExtracted"), "rows": rows,
                               "extraction_summary": summary, "results_api_pages_read": pages,
                               "distinct_ids_via_results_api": len(seen)}
    check(f"Large volume: {load_total} deals extracted across {summary.get('total_pages')} HubSpot pages",
          final.get("status") == "completed" and rows["n"] == rows["distinct_ids"] == load_total,
          f"{seconds}s, {summary.get('batches')} checkpointed batches")
    check("Large volume: results API pages through every row without duplicates",
          len(seen) == load_total, f"{pages} pages of 500")

    # 2. Rate limiting: HubSpot allows only 20 requests / 10 s; the service must back off and finish
    mock.config(rate_limit_per_10s=20, reset_usage=True)
    before = mock.stats()["rate_limited"]
    scan = f"rate-limit-{RUN_ID}"
    code, final, timeline, seconds = run_scan(scan, "org-rate-limit", load_token, {"pageSize": 50})
    limited = mock.stats()["rate_limited"] - before
    rows = rows_for(env, "org-rate-limit", scan)
    mock.config(rate_limit_per_10s=150, reset_usage=True)
    requests_made = (load_total + 49) // 50 + 1  # pages + credential check
    minimum_seconds = (requests_made // 20) * 10  # cannot be faster without exceeding the quota
    details["rate_limiting"] = {"scan_id": scan, "mock_limit_per_10s": 20, "page_size": 50,
                                "hubspot_requests": requests_made, "minimum_compliant_seconds": minimum_seconds,
                                "http_429_returned_by_hubspot": limited, "seconds": seconds,
                                "final_status": final.get("status"), "rows": rows}
    check("Rate limit: a 20 requests / 10 s quota is respected and the scan still completes",
          final.get("status") == "completed" and rows["n"] == load_total and seconds >= minimum_seconds,
          f"{requests_made} requests in {seconds}s (>= {minimum_seconds}s), {limited} x 429 received and retried, "
          f"{rows['n']} rows")

    # 3. Transient outage: two 502s are retried transparently
    mock.fault(status=502, count=2)
    scan = f"transient-5xx-{RUN_ID}"
    code, final, timeline, seconds = run_scan(scan, "org-hubspot-faults", test_token)
    details["transient_5xx"] = {"scan_id": scan, "final_status": final.get("status"),
                                "records": final.get("recordsExtracted")}
    check("Transient 502s are retried and the scan completes",
          final.get("status") == "completed" and final.get("recordsExtracted") == test_total,
          f"{final.get('recordsExtracted')} deals")

    # 4. Outage mid-scan: fails with details, then resumes from the checkpoint once HubSpot recovers
    mock.fault(status=503, count=8, skip=3)      # credential check + 2 pages succeed, then 503s
    scan = f"outage-resume-{RUN_ID}"
    code, failed, timeline, seconds = run_scan(scan, "org-hubspot-outage", test_token,
                                               {"pageSize": 1, "checkpointInterval": 1})
    mock.clear_faults()
    rows_failed = rows_for(env, "org-hubspot-outage", scan)
    resume = call("POST", f"/scan/{scan}/resume")
    final, timeline2 = wait_for(scan, lambda d: d.get("status") in TERMINAL, timeout=300, interval=1)
    rows_final = rows_for(env, "org-hubspot-outage", scan)
    details["outage_then_resume"] = {
        "scan_id": scan, "failed_status": failed.get("status"), "error_message": failed.get("errorMessage"),
        "checkpoint_at_failure": latest_checkpoint(failed), "rows_loaded_before_failure": rows_failed,
        "resume_status_code": resume.status_code, "final_status": final.get("status"),
        "final_rows": rows_final, "records_extracted": final.get("recordsExtracted"),
    }
    check("HubSpot outage mid-scan marks the scan failed with the HubSpot error",
          failed.get("status") == "failed" and "503" in (failed.get("errorMessage") or ""),
          f"{rows_failed['n']} rows committed; error: {(failed.get('errorMessage') or '')[:80]}")
    check("Failed scan resumes from its checkpoint after recovery without duplicates",
          resume.status_code == 202 and final.get("status") == "completed"
          and rows_final["n"] == rows_final["distinct_ids"] == test_total,
          f"{rows_final['n']} rows, recordsExtracted={final.get('recordsExtracted')}")

    # 5. Daily limit: not retried, fails immediately with HubSpot's message
    mock.fault(status=429, count=1, policy="DAILY")
    scan = f"daily-limit-{RUN_ID}"
    code, final, timeline, seconds = run_scan(scan, "org-hubspot-faults", test_token)
    mock.clear_faults()
    details["daily_limit"] = {"scan_id": scan, "final_status": final.get("status"),
                              "error_message": final.get("errorMessage"), "seconds": seconds}
    check("Daily limit 429 fails the scan without retrying",
          final.get("status") == "failed" and "daily limit" in (final.get("errorMessage") or "").lower(),
          final.get("errorMessage"))

    # 6. Missing scope
    scan = f"missing-scope-{RUN_ID}"
    code, final, timeline, seconds = run_scan(scan, "org-hubspot-faults", noscope_token)
    details["missing_scope"] = {"scan_id": scan, "final_status": final.get("status"),
                                "error_message": final.get("errorMessage")}
    check("Token without crm.objects.deals.read fails with a scope error",
          final.get("status") == "failed" and "crm.objects.deals.read" in (final.get("errorMessage") or ""),
          final.get("errorMessage"))

    # 7. Archived deals: archive one deal in the load account, extract with archived=true
    first = mock.hubspot("GET", "/crm/v3/objects/deals", load_token, params={"limit": 1}).json()["results"][0]
    mock.hubspot("DELETE", f"/crm/v3/objects/deals/{first['id']}", load_token)
    archived_total = mock.stats()["accounts"]["load"]["archived"]
    scan = f"archived-{RUN_ID}"
    code, final, timeline, seconds = run_scan(scan, "org-load-archived", load_token, {"archived": True})
    records = call("GET", f"/results/{scan}/result").json()["data"]["records"]
    details["archived"] = {"scan_id": scan, "archived_in_hubspot": archived_total,
                           "records": [{k: r[k] for k in ("id", "dealname", "archived", "archived_at")} for r in records]}
    check("archived=true extracts only archived deals with archived_at",
          final.get("status") == "completed" and len(records) == archived_total
          and all(r["archived"] and r["archived_at"] for r in records) and first["id"] in {r["id"] for r in records},
          f"{len(records)} archived deal(s)")

    details["mock_stats_after"] = mock.stats()
    passed = sum(c["passed"] for c in checks)
    details["checks"] = checks
    details["summary"] = {"passed": passed, "total": len(checks)}
    write_json("resilience_test.json", details)
    lines = [f"# Resilience tests against the HubSpot mock ({RUN_ID})", "",
             f"**{passed}/{len(checks)} checks passed**", "", "| Result | Check | Detail |", "|---|---|---|"]
    lines += [f"| {'PASS' if c['passed'] else 'FAIL'} | {c['name']} | {str(c['detail'] or '').replace('|', '/')} |"
              for c in checks]
    write_text("resilience_test.md", "\n".join(lines) + "\n")
    print(f"\n{passed}/{len(checks)} resilience checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
