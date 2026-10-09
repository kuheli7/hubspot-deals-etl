"""
HMAC request signing with the coordinator key (COORDINATOR_KEY).

Every request under /api/v1 must send two headers:

    X-Coordinator-Timestamp: current unix time in seconds
    X-Coordinator-Signature: hex HMAC-SHA256, keyed with COORDINATOR_KEY, of
        "<timestamp>\\n<METHOD>\\n<path?query>\\n" followed by the raw request body

Requests more than 5 minutes old (or ahead) are rejected, so a captured
request cannot be replayed later. The swagger spec stays open so /docs/ loads.
"""
import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import unquote, urlsplit

import requests
from flask import request

TIMESTAMP_HEADER = "X-Coordinator-Timestamp"
SIGNATURE_HEADER = "X-Coordinator-Signature"
MAX_AGE_SECONDS = 300


def sign(key: str, timestamp: str, method: str, path: str, body: Optional[bytes] = b"") -> str:
    """Signature for one request; used by the service and by its clients"""
    message = f"{timestamp}\n{method.upper()}\n{path}\n".encode() + (body or b"")
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


def verify_request(key: str, prefix: str = "/api/v1"):
    """
    Flask before_request check. Returns None when the request may continue,
    otherwise a 401 response.
    """
    if not request.path.startswith(prefix + "/") or request.method == "OPTIONS":
        return None
    if request.path == f"{prefix}/swagger.json":
        return None

    timestamp = request.headers.get(TIMESTAMP_HEADER, "")
    signature = request.headers.get(SIGNATURE_HEADER, "")
    if not timestamp or not signature:
        return unauthorized(f"Missing {TIMESTAMP_HEADER} or {SIGNATURE_HEADER} header")
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return unauthorized(f"{TIMESTAMP_HEADER} must be unix time in seconds")
    if age > MAX_AGE_SECONDS:
        return unauthorized("Request timestamp is more than 5 minutes off")

    path = request.path
    if request.query_string:
        path += "?" + request.query_string.decode()
    expected = sign(key, timestamp, request.method, path, request.get_data())
    # compare bytes: compare_digest raises on non-ASCII str, which would be a 500
    if not hmac.compare_digest(expected.encode(), signature.lower().encode()):
        return unauthorized("Invalid request signature")
    return None


def unauthorized(message: str):
    return {"success": False, "message": message, "error": "Unauthorized"}, 401


class CoordinatorAuth(requests.auth.AuthBase):
    """Signs outgoing `requests` calls, e.g. requests.get(url, auth=CoordinatorAuth(key))"""

    def __init__(self, key: str):
        self.key = key

    def __call__(self, r):
        parts = urlsplit(r.url)
        path = unquote(parts.path) + (f"?{parts.query}" if parts.query else "")
        body = r.body.encode() if isinstance(r.body, str) else r.body
        timestamp = str(int(time.time()))
        r.headers[TIMESTAMP_HEADER] = timestamp
        r.headers[SIGNATURE_HEADER] = sign(self.key, timestamp, r.method, path, body)
        return r
