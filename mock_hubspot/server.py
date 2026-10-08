"""
Local mock of the HubSpot CRM v3 REST API (deals).

Implements the HubSpot endpoints, request parameters, response bodies, error
bodies and rate-limit headers that the ETL service depends on, so the service
can be developed and tested without a HubSpot account. The ETL code is not
aware of the mock: pointing HUBSPOT_API_BASE_URL at it is the only change.

HubSpot endpoints
  GET    /crm/v3/objects/deals                 list (limit, after, properties,
                                               propertiesWithHistory, associations, archived)
  POST   /crm/v3/objects/deals                 create
  GET    /crm/v3/objects/deals/{dealId}        read one
  PATCH  /crm/v3/objects/deals/{dealId}        update
  DELETE /crm/v3/objects/deals/{dealId}        archive (204)
  GET    /crm/v3/properties/deals[/{name}]     property definitions
  GET    /crm/v3/pipelines/deals[/{id}]        pipelines and stages
  GET    /account-info/v3/details              account (portal) details
  GET    /account-info/v3/api-usage/daily/private-apps

Mock-only admin endpoints (not part of HubSpot)
  GET    /__mock/health | /__mock/stats
  PUT    /__mock/config      {"rate_limit_per_10s": 150, "latency_ms": 0}
  POST   /__mock/faults      {"status": 429|500|502|503, "count": 2, "skip": 0,
                              "policy": "TEN_SECONDLY_ROLLING"|"DAILY"}
  DELETE /__mock/faults
  POST   /__mock/reset       {"account": "test"}

    python -m mock_hubspot.server            # port 5299 (MOCK_HUBSPOT_PORT)
"""
import json
import os
import random
import threading
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from flask import Flask, Response, jsonify, request

from .catalog import (
    ALWAYS_RETURNED_PROPERTIES,
    DEFAULT_RETURNED_PROPERTIES,
    PIPELINES,
    PROPERTY_DEFINITIONS,
    READ_ONLY_PROPERTIES,
    pipeline_payload,
    stage_index,
)

READ = "crm.objects.deals.read"
WRITE = "crm.objects.deals.write"
WINDOW_MS = 10000
MAX_LIMIT = 100
MAX_LIMIT_WITH_HISTORY = 50

# Exact body HubSpot returns for a missing or unknown token
MESSAGE_401 = ("Authentication credentials not found. This API supports OAuth 2.0 authentication and you can "
               "find more details at https://developers.hubspot.com/docs/methods/auth/oauth-overview")
MESSAGE_403 = ("This app hasn't been granted all required scopes to make this call. "
               "Read more about required scopes here: https://developers.hubspot.com/scopes.")

STAGES = stage_index()


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #
def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def now_iso() -> str:
    return iso(datetime.now(timezone.utc))


def parse_datetime(value: Any) -> Optional[datetime]:
    """HubSpot accepts ISO-8601 datetimes, YYYY-MM-DD dates and epoch milliseconds"""
    text = str(value).strip()
    try:
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
        if len(text) == 10:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fmt_decimal(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def error_body(message: str, category: str, **extra) -> Dict[str, Any]:
    return {"status": "error", "message": message, "correlationId": str(uuid.uuid4()), "category": category, **extra}


def mask(token: Optional[str]) -> str:
    return "<none>" if not token else f"{token[:8]}...{token[-4:]}"


# ---------------------------------------------------------------------- #
# Account store
# ---------------------------------------------------------------------- #
class AccountStore:
    """Deals per mock HubSpot account, persisted to a JSON file"""

    def __init__(self, data_file: Optional[str], load_deals: int):
        self.data_file = data_file
        self.lock = threading.RLock()
        self.accounts: Dict[str, Dict[str, Any]] = {
            "test": {"portal_id": 48600123, "name": "Deals ETL Test Account", "next_id": 40100000001, "deals": {}},
            "load": {"portal_id": 48600456, "name": "Deals ETL Load Test Account", "next_id": 50200000001, "deals": {}},
        }
        self._load()
        if load_deals and not self.accounts["load"]["deals"]:
            self._seed_load_account(load_deals)
            self.save()

    def _load(self) -> None:
        if self.data_file and os.path.exists(self.data_file):
            with open(self.data_file, encoding="utf-8") as fh:
                saved = json.load(fh)
            for name, account in saved.get("accounts", {}).items():
                if name in self.accounts:
                    self.accounts[name].update(account)

    def save(self) -> None:
        if not self.data_file:
            return
        with self.lock:
            os.makedirs(os.path.dirname(self.data_file) or ".", exist_ok=True)
            tmp = self.data_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"accounts": self.accounts}, fh)
            os.replace(tmp, self.data_file)

    def _seed_load_account(self, count: int) -> None:
        rng = random.Random(42)
        companies = ["Acme", "Globex", "Initech", "Umbrella", "Stark", "Wayne", "Wonka", "Hooli", "Pied Piper", "Vandelay"]
        products = ["Starter Plan", "Annual Subscription", "Enterprise Expansion", "Platform Migration", "Support Renewal"]
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        for i in range(count):
            created = base + timedelta(hours=rng.randint(0, 24 * 600))
            stage = rng.choice(list(STAGES))
            properties = {
                "dealname": f"{rng.choice(companies)} - {rng.choice(products)} #{i + 1}",
                "amount": str(rng.choice([500, 1200, 5000, 9999.99, 25000, 50000, 75000, 100000, 250000])),
                "dealstage": stage,
                "pipeline": "default",
                "dealtype": rng.choice(["newbusiness", "existingbusiness"]),
                "hs_priority": rng.choice(["low", "medium", "high"]),
                "closedate": iso(created + timedelta(days=rng.randint(7, 120))),
                "description": f"Generated load-test deal {i + 1}",
            }
            self._insert("load", properties, created_at=created)

    def _insert(self, account: str, properties: Dict[str, Any], created_at: Optional[datetime] = None) -> Dict[str, Any]:
        acct = self.accounts[account]
        deal_id = str(acct["next_id"])
        acct["next_id"] += 1
        created = created_at or datetime.now(timezone.utc)
        stamp = iso(created)
        deal = {
            "id": deal_id,
            "properties": {},
            "history": {},
            "createdAt": stamp,
            "updatedAt": stamp,
            "archived": False,
            "archivedAt": None,
        }
        props = {"createdate": stamp, **properties}
        self._apply(deal, props, stamp)
        acct["deals"][deal_id] = deal
        return deal

    @staticmethod
    def _apply(deal: Dict[str, Any], properties: Dict[str, Any], stamp: str) -> None:
        props = deal["properties"]
        for name, value in properties.items():
            props[name] = value
            deal["history"].setdefault(name, []).insert(
                0, {"value": value, "timestamp": stamp, "sourceType": "INTEGRATION", "sourceId": "private-app"}
            )
        props.setdefault("pipeline", "default")
        props["hs_object_id"] = deal["id"]
        props["hs_lastmodifieddate"] = stamp
        props["hs_object_source"] = "INTEGRATION"
        props.setdefault("num_associated_contacts", "0")

        stage = STAGES.get(props.get("dealstage") or "")
        if stage:
            props["hs_deal_stage_probability"] = stage["probability"]
            props["hs_is_closed"] = "true" if stage["is_closed"] else "false"
            props["hs_is_closed_won"] = "true" if props["dealstage"] == "closedwon" else "false"
            props["hs_is_closed_lost"] = "true" if props["dealstage"] == "closedlost" else "false"
        amount = None
        try:
            amount = Decimal(str(props["amount"])) if props.get("amount") not in (None, "") else None
        except InvalidOperation:
            amount = None
        if amount is not None:
            props["amount_in_home_currency"] = fmt_decimal(amount)
            if stage:
                props["hs_projected_amount"] = fmt_decimal(amount * Decimal(stage["probability"]))
                props["hs_closed_amount"] = fmt_decimal(amount) if props["dealstage"] == "closedwon" else "0"
        created = parse_datetime(props.get("createdate"))
        closed = parse_datetime(props["closedate"]) if props.get("closedate") else None
        if created and closed:
            props["days_to_close"] = str(max(0, (closed.date() - created.date()).days))
        deal["updatedAt"] = stamp

    def create(self, account: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            deal = self._insert(account, properties)
            self.save()
            return deal

    def update(self, account: str, deal_id: str, properties: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self.lock:
            deal = self.accounts[account]["deals"].get(deal_id)
            if not deal or deal["archived"]:
                return None
            self._apply(deal, properties, now_iso())
            self.save()
            return deal

    def archive(self, account: str, deal_id: str) -> bool:
        with self.lock:
            deal = self.accounts[account]["deals"].get(deal_id)
            if not deal or deal["archived"]:
                return False
            stamp = now_iso()
            deal["archived"] = True
            deal["archivedAt"] = stamp
            deal["updatedAt"] = stamp
            self.save()
            return True

    def deals(self, account: str, archived: bool) -> List[Dict[str, Any]]:
        with self.lock:
            return sorted(
                (d for d in self.accounts[account]["deals"].values() if d["archived"] == archived),
                key=lambda d: int(d["id"]),
            )

    def get(self, account: str, deal_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            return self.accounts[account]["deals"].get(deal_id)

    def reset(self, account: str) -> None:
        with self.lock:
            self.accounts[account]["deals"] = {}
            self.save()


# ---------------------------------------------------------------------- #
# Validation of property writes (create / update)
# ---------------------------------------------------------------------- #
def validate_properties(properties: Dict[str, Any]) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    clean: Dict[str, str] = {}
    errors: List[Dict[str, Any]] = []

    def fail(name, error, message):
        errors.append({"isValid": False, "message": message, "error": error, "name": name})

    for name, raw in properties.items():
        definition = PROPERTY_DEFINITIONS.get(name)
        if definition is None:
            fail(name, "PROPERTY_DOESNT_EXIST", f'Property "{name}" does not exist')
            continue
        if name in READ_ONLY_PROPERTIES:
            fail(name, "READ_ONLY_VALUE", f'"{name}" is a read only property; its value cannot be set.')
            continue
        if raw is None or str(raw) == "":
            clean[name] = ""
            continue
        value = str(raw).strip()
        kind = definition["type"]
        if kind == "number":
            try:
                Decimal(value)
            except InvalidOperation:
                fail(name, "INVALID_DECIMAL", f"{value} was not a valid number.")
                continue
        elif kind in ("datetime", "date"):
            parsed = parse_datetime(value)
            if not parsed:
                fail(name, "INVALID_DATE", f"{value} was not a valid date.")
                continue
            value = parsed.strftime("%Y-%m-%dT%H:%M:%SZ") if name == "closedate" else iso(parsed)
        elif kind == "enumeration" and definition["options"]:
            allowed = {o["value"] for o in definition["options"]}
            if value not in allowed:
                fail(name, "INVALID_OPTION",
                     f"{value} was not one of the allowed options: {sorted(allowed)}")
                continue
        clean[name] = value

    stage = clean.get("dealstage")
    pipeline = clean.get("pipeline", "default")
    if stage and stage in STAGES and STAGES[stage]["pipeline"] != pipeline:
        fail("dealstage", "INVALID_OPTION", f"{stage} is not a stage of pipeline {pipeline}")
    return clean, errors


# ---------------------------------------------------------------------- #
# Application
# ---------------------------------------------------------------------- #
def create_app(
    data_file: Optional[str] = None,
    load_deals: Optional[int] = None,
    rate_limit_per_10s: Optional[int] = None,
    tokens: Optional[Dict[str, Tuple[str, Set[str]]]] = None,
) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False

    store = AccountStore(
        data_file if data_file is not None else os.environ.get("MOCK_HUBSPOT_DATA_FILE"),
        load_deals if load_deals is not None else int(os.environ.get("MOCK_HUBSPOT_LOAD_DEALS", "2500")),
    )
    token_map = tokens or {
        os.environ.get("MOCK_HUBSPOT_TOKEN", "pat-mock-test-account-deals"): ("test", {READ, WRITE}),
        os.environ.get("MOCK_HUBSPOT_READONLY_TOKEN", "pat-mock-test-account-readonly"): ("test", {READ}),
        os.environ.get("MOCK_HUBSPOT_NO_SCOPE_TOKEN", "pat-mock-test-account-noscope"): ("test", set()),
        os.environ.get("MOCK_HUBSPOT_LOAD_TOKEN", "pat-mock-load-account-deals"): ("load", {READ, WRITE}),
    }
    settings = {
        "rate_limit_per_10s": rate_limit_per_10s or int(os.environ.get("MOCK_HUBSPOT_RATE_LIMIT", "150")),
        "daily_limit": int(os.environ.get("MOCK_HUBSPOT_DAILY_LIMIT", "250000")),
        "latency_ms": int(os.environ.get("MOCK_HUBSPOT_LATENCY_MS", "0")),
    }
    windows: Dict[str, Deque[float]] = {}
    daily_usage: Counter = Counter()
    faults: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {"requests": 0, "by_status": Counter(), "rate_limited": 0, "faults_served": 0,
                             "started_at": now_iso()}
    lock = threading.Lock()

    def rate_headers(token: str, account: str) -> Dict[str, str]:
        window = windows.get(token, deque())
        return {
            "X-HubSpot-RateLimit-Daily": str(settings["daily_limit"]),
            "X-HubSpot-RateLimit-Daily-Remaining": str(max(0, settings["daily_limit"] - daily_usage[account])),
            "X-HubSpot-RateLimit-Interval-Milliseconds": str(WINDOW_MS),
            "X-HubSpot-RateLimit-Max": str(settings["rate_limit_per_10s"]),
            "X-HubSpot-RateLimit-Remaining": str(max(0, settings["rate_limit_per_10s"] - len(window))),
        }

    def respond(body: Any, status: int, headers: Optional[Dict[str, str]] = None) -> Response:
        response = jsonify(body) if body is not None else Response(status=status)
        response.status_code = status
        response.headers["X-HubSpot-Correlation-Id"] = (body or {}).get("correlationId", str(uuid.uuid4())) \
            if isinstance(body, dict) else str(uuid.uuid4())
        for key, value in (headers or {}).items():
            response.headers[key] = value
        return response

    def hubspot_endpoint(scope: str):
        """Auth, fault injection, rate limiting and scope checks shared by every HubSpot route"""

        def decorator(handler):
            @wraps(handler)
            def wrapper(*args, **kwargs):
                with lock:
                    if faults and request.path.startswith(faults[0].get("path", "/crm/")):
                        fault = faults[0]
                        if fault.get("skip", 0) > 0:
                            fault["skip"] -= 1          # let the first N matching requests through
                        else:
                            fault["count"] -= 1
                            if fault["count"] <= 0:
                                faults.pop(0)
                            stats["faults_served"] += 1
                            return fault_response(fault)

                header = request.headers.get("Authorization", "")
                token = header[7:].strip() if header.startswith("Bearer ") else ""
                if token not in token_map:
                    return respond(error_body(MESSAGE_401, "INVALID_AUTHENTICATION"), 401)
                account, scopes = token_map[token]

                with lock:
                    now = time.monotonic()
                    window = windows.setdefault(token, deque())
                    while window and now - window[0] >= WINDOW_MS / 1000:
                        window.popleft()
                    if daily_usage[account] >= settings["daily_limit"]:
                        stats["rate_limited"] += 1
                        return respond(error_body("You have reached your daily limit.", "RATE_LIMITS",
                                                  errorType="RATE_LIMIT", policyName="DAILY"),
                                       429, rate_headers(token, account))
                    if len(window) >= settings["rate_limit_per_10s"]:
                        stats["rate_limited"] += 1
                        return respond(error_body("You have reached your ten_secondly_rolling limit.", "RATE_LIMITS",
                                                  errorType="RATE_LIMIT", policyName="TEN_SECONDLY_ROLLING"),
                                       429, rate_headers(token, account))
                    window.append(now)
                    daily_usage[account] += 1

                if scope and scope not in scopes:
                    return respond(error_body(
                        MESSAGE_403, "MISSING_SCOPES",
                        errors=[{"message": "One or more of the following scopes are required.",
                                 "context": {"requiredGranularScopes": [scope]}}],
                        links={"scopes": "https://developers.hubspot.com/scopes"},
                    ), 403, rate_headers(token, account))

                if settings["latency_ms"]:
                    time.sleep(settings["latency_ms"] / 1000)

                body, status = handler(account, *args, **kwargs)
                return respond(body, status, rate_headers(token, account))

            return wrapper

        return decorator

    def fault_response(fault: Dict[str, Any]) -> Response:
        status = int(fault.get("status", 500))
        if status == 429:
            policy = fault.get("policy", "TEN_SECONDLY_ROLLING")
            message = ("You have reached your daily limit." if policy == "DAILY"
                       else "You have reached your ten_secondly_rolling limit.")
            return respond(error_body(message, "RATE_LIMITS", errorType="RATE_LIMIT", policyName=policy), 429,
                           {"X-HubSpot-RateLimit-Interval-Milliseconds": str(fault.get("interval_ms", 1000))})
        return respond(error_body("An internal error occurred. Please try again later.", "INTERNAL_ERROR"), status)

    def select_properties(deal: Dict[str, Any], requested: Optional[List[str]]) -> Dict[str, Any]:
        props = deal["properties"]
        if requested is None:
            names = DEFAULT_RETURNED_PROPERTIES
        else:
            names = [n for n in requested if n in PROPERTY_DEFINITIONS] + ALWAYS_RETURNED_PROPERTIES
        selected = {}
        for name in names:
            value = props.get(name)
            selected[name] = None if value in (None, "") else value
        return dict(sorted(selected.items()))

    def serialize(deal: Dict[str, Any], requested: Optional[List[str]], with_history: Optional[List[str]] = None):
        body = {
            "id": deal["id"],
            "properties": select_properties(deal, requested),
            "createdAt": deal["createdAt"],
            "updatedAt": deal["updatedAt"],
            "archived": deal["archived"],
        }
        if deal["archived"]:
            body["archivedAt"] = deal["archivedAt"]
        if with_history:
            body["propertiesWithHistory"] = {
                name: deal["history"].get(name, []) for name in with_history if name in PROPERTY_DEFINITIONS
            }
        return body

    def list_param(name: str) -> Optional[List[str]]:
        values = request.args.getlist(name)
        if not values:
            return None
        return [p.strip() for v in values for p in v.split(",") if p.strip()]

    def bool_param(name: str) -> bool:
        return request.args.get(name, "false").strip().lower() == "true"

    def validation_error(message: str):
        return error_body(message, "VALIDATION_ERROR"), 400

    # ------------------------------------------------------------------ #
    # Deals
    # ------------------------------------------------------------------ #
    @app.get("/crm/v3/objects/deals")
    @hubspot_endpoint(READ)
    def list_deals(account):
        requested = list_param("properties")
        history = list_param("propertiesWithHistory")
        try:
            limit = int(request.args.get("limit", "10"))
        except ValueError:
            return validation_error(f"Unable to parse limit: {request.args.get('limit')}")
        if limit < 1:
            return validation_error("limit must be at least 1")
        limit = min(limit, MAX_LIMIT_WITH_HISTORY if history else MAX_LIMIT)
        after = request.args.get("after")
        if after is not None and not after.isdigit():
            return validation_error(f"Unable to parse after token: {after}")

        deals = store.deals(account, bool_param("archived"))
        if after:
            deals = [d for d in deals if int(d["id"]) >= int(after)]
        page, rest = deals[:limit], deals[limit:]
        body: Dict[str, Any] = {"results": [serialize(d, requested, history) for d in page]}
        if rest:
            next_after = rest[0]["id"]
            body["paging"] = {"next": {
                "after": next_after,
                "link": f"{request.host_url}crm/v3/objects/deals?limit={limit}&after={next_after}",
            }}
        return body, 200

    @app.post("/crm/v3/objects/deals")
    @hubspot_endpoint(WRITE)
    def create_deal(account):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("properties"), dict):
            return validation_error("Invalid input JSON: expected an object with a 'properties' object")
        clean, errors = validate_properties(payload["properties"])
        if errors:
            return validation_error(f"Property values were not valid: {json.dumps(errors)}")
        deal = store.create(account, clean)
        return serialize(deal, [k for k, v in deal["properties"].items() if v not in (None, "")]), 201

    @app.get("/crm/v3/objects/deals/<deal_id>")
    @hubspot_endpoint(READ)
    def get_deal(account, deal_id):
        deal = store.get(account, deal_id)
        if not deal or (deal["archived"] and not bool_param("archived")):
            return error_body("Object not found.  objectId are usually numeric.", "OBJECT_NOT_FOUND",
                              context={"id": [deal_id]}), 404
        return serialize(deal, list_param("properties"), list_param("propertiesWithHistory")), 200

    @app.patch("/crm/v3/objects/deals/<deal_id>")
    @hubspot_endpoint(WRITE)
    def update_deal(account, deal_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("properties"), dict):
            return validation_error("Invalid input JSON: expected an object with a 'properties' object")
        clean, errors = validate_properties(payload["properties"])
        if errors:
            return validation_error(f"Property values were not valid: {json.dumps(errors)}")
        deal = store.update(account, deal_id, clean)
        if not deal:
            return error_body("Object not found.  objectId are usually numeric.", "OBJECT_NOT_FOUND",
                              context={"id": [deal_id]}), 404
        return serialize(deal, [k for k, v in deal["properties"].items() if v not in (None, "")]), 200

    @app.delete("/crm/v3/objects/deals/<deal_id>")
    @hubspot_endpoint(WRITE)
    def archive_deal(account, deal_id):
        store.archive(account, deal_id)  # HubSpot returns 204 even if already archived
        return None, 204

    # ------------------------------------------------------------------ #
    # Properties, pipelines, account
    # ------------------------------------------------------------------ #
    @app.get("/crm/v3/properties/deals")
    @hubspot_endpoint(READ)
    def list_properties(account):
        return {"results": list(PROPERTY_DEFINITIONS.values())}, 200

    @app.get("/crm/v3/properties/deals/<name>")
    @hubspot_endpoint(READ)
    def get_property(account, name):
        definition = PROPERTY_DEFINITIONS.get(name)
        if not definition:
            return error_body(f"Unable to find property {name}", "OBJECT_NOT_FOUND"), 404
        return definition, 200

    @app.get("/crm/v3/pipelines/deals")
    @hubspot_endpoint(READ)
    def list_pipelines(account):
        return {"results": [pipeline_payload(p) for p in PIPELINES]}, 200

    @app.get("/crm/v3/pipelines/deals/<pipeline_id>")
    @hubspot_endpoint(READ)
    def get_pipeline(account, pipeline_id):
        for pipeline in PIPELINES:
            if pipeline["id"] == pipeline_id:
                return pipeline_payload(pipeline), 200
        return error_body(f"Unable to find pipeline {pipeline_id}", "OBJECT_NOT_FOUND"), 404

    @app.get("/account-info/v3/details")
    @hubspot_endpoint("")
    def account_details(account):
        acct = store.accounts[account]
        return {
            "portalId": acct["portal_id"],
            "accountType": "DEVELOPER_TEST",
            "timeZone": "US/Eastern",
            "companyCurrency": "USD",
            "additionalCurrencies": [],
            "utcOffset": "-04:00",
            "utcOffsetMilliseconds": -14400000,
            "uiDomain": "app.hubspot.com",
            "dataHostingLocation": "na1",
        }, 200

    @app.get("/account-info/v3/api-usage/daily/private-apps")
    @hubspot_endpoint("")
    def api_usage(account):
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return {"results": [{
            "name": "private-apps-api-calls-daily",
            "usageLimit": settings["daily_limit"],
            "currentUsage": daily_usage[account],
            "collectedAt": now_iso(),
            "fetchStatus": "SUCCESS",
            "resetsAt": iso(tomorrow),
        }]}, 200

    # ------------------------------------------------------------------ #
    # Mock admin endpoints
    # ------------------------------------------------------------------ #
    @app.get("/__mock/health")
    def mock_health():
        return {"status": "ok", "accounts": {k: len(v["deals"]) for k, v in store.accounts.items()}}

    @app.get("/__mock/stats")
    def mock_stats():
        return {
            "requests": stats["requests"],
            "by_status": dict(stats["by_status"]),
            "rate_limited": stats["rate_limited"],
            "faults_served": stats["faults_served"],
            "pending_faults": faults,
            "settings": settings,
            "daily_usage": dict(daily_usage),
            "accounts": {k: {"portal_id": v["portal_id"], "deals": len(v["deals"]),
                             "archived": sum(1 for d in v["deals"].values() if d["archived"])}
                         for k, v in store.accounts.items()},
            "started_at": stats["started_at"],
        }

    @app.put("/__mock/config")
    def mock_config():
        payload = request.get_json(silent=True) or {}
        for key in ("rate_limit_per_10s", "daily_limit", "latency_ms"):
            if key in payload:
                settings[key] = int(payload[key])
        if payload.get("reset_usage"):
            daily_usage.clear()
            windows.clear()
        return {"settings": settings}

    @app.post("/__mock/faults")
    def add_fault():
        payload = request.get_json(silent=True) or {}
        fault = {"status": int(payload.get("status", 500)), "count": int(payload.get("count", 1)),
                 "path": payload.get("path", "/crm/v3/objects/deals"),
                 "policy": payload.get("policy", "TEN_SECONDLY_ROLLING"),
                 "interval_ms": int(payload.get("interval_ms", 1000)),
                 "skip": int(payload.get("skip", 0))}
        with lock:
            faults.append(fault)
        return {"queued": fault}, 201

    @app.delete("/__mock/faults")
    def clear_faults():
        with lock:
            faults.clear()
        return {"cleared": True}

    @app.post("/__mock/reset")
    def reset_account():
        payload = request.get_json(silent=True) or {}
        account = payload.get("account", "test")
        if account not in store.accounts:
            return {"error": f"unknown account {account}"}, 400
        store.reset(account)
        return {"reset": account}

    # ------------------------------------------------------------------ #
    @app.after_request
    def record(response):
        if not request.path.startswith("/__mock"):
            with lock:
                stats["requests"] += 1
                stats["by_status"][str(response.status_code)] += 1
            header = request.headers.get("Authorization", "")
            app.logger.info("%s %s -> %s (token %s)", request.method, request.full_path.rstrip("?"),
                            response.status_code, mask(header[7:] if header.startswith("Bearer ") else None))
        return response

    @app.errorhandler(404)
    def not_found(_):
        return respond(error_body(f"Unable to route {request.method} {request.path}", "OBJECT_NOT_FOUND"), 404)

    @app.errorhandler(405)
    def method_not_allowed(_):
        return respond(error_body(f"Method {request.method} not allowed", "VALIDATION_ERROR"), 405)

    app.extensions["mock_store"] = store
    app.extensions["mock_settings"] = settings
    return app


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    port = int(os.environ.get("MOCK_HUBSPOT_PORT", "5299"))
    app = create_app()
    app.logger.setLevel(logging.INFO)
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
