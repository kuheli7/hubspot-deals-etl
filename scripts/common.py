"""Shared helpers for the scripts in this folder (no third-party dependencies beyond requests)."""
import json
import os
import pathlib
import re
from typing import Any, Dict

# Keep the service's JSON logger quiet when its modules are imported by scripts
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOKI_ENABLED", "false")

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
TEST_RESULTS = pathlib.Path(os.environ.get("TEST_RESULTS_DIR") or PROJECT_ROOT / "test-results")
TOKEN_PATTERN = re.compile(r"pat-[a-z0-9]+-[0-9a-f-]{20,}", re.IGNORECASE)
_SECRETS: list = []  # exact secret values to scrub, registered by get_access_token


def load_env(path: pathlib.Path = PROJECT_ROOT / ".env") -> Dict[str, str]:
    """Read KEY=VALUE pairs from .env; real environment variables take precedence"""
    values: Dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.split(" #", 1)[0]  # allow inline comments like docker compose
            values[key.strip()] = value.strip().strip('"').strip("'")
    values.update({k: v for k, v in os.environ.items() if k.startswith(("HUBSPOT_", "DB_", "SERVICE_", "COORDINATOR_"))})
    return values


def get_access_token(env: Dict[str, str]) -> str:
    token = env.get("HUBSPOT_ACCESS_TOKEN", "")
    if not token or "your-token-here" in token:
        raise SystemExit(
            "HUBSPOT_ACCESS_TOKEN is not set. Put your private app token in .env "
            "(HUBSPOT_ACCESS_TOKEN=pat-...) - never commit it."
        )
    if token not in _SECRETS:
        _SECRETS.append(token)
    return token


def scrub(value: Any) -> Any:
    """Remove anything that looks like a HubSpot private app token"""
    if isinstance(value, str):
        for secret in _SECRETS:
            value = value.replace(secret, "***REDACTED***")
        return TOKEN_PATTERN.sub("pat-***REDACTED***", value)
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: ("***REDACTED***" if k.lower() in ("accesstoken", "access_token", "authorization") else scrub(v))
                for k, v in value.items()}
    return value


def write_json(name: str, payload: Any) -> pathlib.Path:
    TEST_RESULTS.mkdir(exist_ok=True)
    path = TEST_RESULTS / name
    path.write_text(json.dumps(scrub(payload), indent=2, default=str) + "\n", encoding="utf-8")
    return path


def write_text(name: str, text: str) -> pathlib.Path:
    TEST_RESULTS.mkdir(exist_ok=True)
    path = TEST_RESULTS / name
    path.write_text(scrub(text), encoding="utf-8")
    return path
