"""
Send one HMAC-signed request to the service API and print the response.
Every /api/v1 call needs the coordinator signature; this reads COORDINATOR_KEY
from .env and signs the request for you.

    python scripts/signed_request.py GET /scan/list
    python scripts/signed_request.py GET "/results/deals-001/result?limit=10"
    python scripts/signed_request.py POST /scan/start @scan.json
    python scripts/signed_request.py POST /maintenance/cleanup '{"daysOld": 7}'

A body starting with @ is read from that file (easier than quoting JSON in PowerShell).
Set SERVICE_URL to call another instance (default http://localhost:5200).
"""
import json
import pathlib
import sys

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from coordinator_auth import CoordinatorAuth  # noqa: E402
from scripts.common import load_env  # noqa: E402


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    method, path = sys.argv[1].upper(), sys.argv[2]
    body = sys.argv[3] if len(sys.argv) > 3 else None
    if body and body.startswith("@"):
        body = pathlib.Path(body[1:]).read_text(encoding="utf-8")

    env = load_env()
    key = env.get("COORDINATOR_KEY")
    if not key:
        raise SystemExit("COORDINATOR_KEY is not set. Add it to .env (see .env.example).")

    url = env.get("SERVICE_URL", "http://localhost:5200").rstrip("/") + "/api/v1" + path
    response = requests.request(method, url, data=body.encode() if body else None,
                                headers={"Content-Type": "application/json"} if body else {},
                                auth=CoordinatorAuth(key), timeout=60)
    print(response.status_code)
    try:
        print(json.dumps(response.json(), indent=2))
    except ValueError:
        print(response.text)


if __name__ == "__main__":
    main()
