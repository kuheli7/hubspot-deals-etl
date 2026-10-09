"""
HubSpot CRM API client for deal extraction.

Wraps the HubSpot CRM v3 deals endpoints with:
- private app access token (Bearer) authentication, passed per request
- cursor-based pagination (``paging.next.after``)
- client-side rate limiting (default 150 requests per rolling 10 seconds)
  plus adaptive back-off driven by HubSpot's ``X-HubSpot-RateLimit-*`` headers
- retries with exponential back-off for 5xx and network errors; 429s are
  waited out separately and do not use up those retries
- typed exceptions for the common HubSpot error responses

The access token is never logged and never stored on the shared session, so one
client instance cannot leak credentials between tenants.
"""
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional

import requests

from loki_logger import get_logger, log_api_call


# Deal properties requested on every extraction. HubSpot only returns
# ``dealname``, ``amount``, ``closedate``, ``createdate``, ``dealstage``,
# ``pipeline``, ``hs_lastmodifieddate`` and ``hs_object_id`` when no
# ``properties`` parameter is sent, so the full set is requested explicitly.
DEFAULT_DEAL_PROPERTIES: List[str] = [
    "dealname",
    "amount",
    "dealstage",
    "pipeline",
    "dealtype",
    "closedate",
    "createdate",
    "hs_lastmodifieddate",
    "description",
    "hubspot_owner_id",
    "hs_object_id",
    "hs_priority",
    "hs_deal_stage_probability",
    "hs_forecast_amount",
    "hs_projected_amount",
    "amount_in_home_currency",
    "deal_currency_code",
    "hs_is_closed",
    "hs_is_closed_won",
    "days_to_close",
    "hs_closed_amount",
    "closed_lost_reason",
    "closed_won_reason",
    "hs_next_step",
    "hs_analytics_source",
    "num_associated_contacts",
    "num_contacted_notes",
    "notes_last_updated",
]

HUBSPOT_MAX_PAGE_SIZE = 100


class HubSpotAPIError(Exception):
    """Base error for HubSpot API failures"""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        category: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.category = category
        self.correlation_id = correlation_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": type(self).__name__,
            "message": self.message,
            "status_code": self.status_code,
            "category": self.category,
            "correlation_id": self.correlation_id,
        }


class HubSpotAuthenticationError(HubSpotAPIError):
    """401 - token missing, malformed, expired or revoked"""


class HubSpotPermissionError(HubSpotAPIError):
    """403 - token is valid but lacks a required scope"""


class HubSpotNotFoundError(HubSpotAPIError):
    """404 - requested object or endpoint does not exist"""


class HubSpotRateLimitError(HubSpotAPIError):
    """429 - burst or daily rate limit exhausted after retries"""


class HubSpotServerError(HubSpotAPIError):
    """5xx - HubSpot side failure that persisted after retries"""


class RateLimiter:
    """
    Thread-safe sliding-window limiter: at most ``max_requests`` calls in any
    ``window_seconds`` interval. ``acquire`` blocks until a slot is free.
    """

    def __init__(self, max_requests: int = 150, window_seconds: float = 10.0):
        if max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._calls: Deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Reserve a request slot; returns the seconds spent waiting"""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self.max_requests:
                    self._calls.append(now)
                    return waited
                sleep_for = self.window_seconds - (now - self._calls[0])
            sleep_for = max(sleep_for, 0.01)
            time.sleep(sleep_for)
            waited += sleep_for


class HubSpotAPIService:
    """Client for the HubSpot CRM v3 deals API"""

    def __init__(
        self,
        base_url: str = "https://api.hubapi.com",
        deals_endpoint: str = "/crm/v3/objects/deals",
        properties_endpoint: str = "/crm/v3/properties/deals",
        pipelines_endpoint: str = "/crm/v3/pipelines/deals",
        timeout: int = 30,
        max_requests_per_window: int = 150,
        window_seconds: float = 10.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        max_rate_limit_waits: int = 10,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.deals_endpoint = deals_endpoint
        self.properties_endpoint = properties_endpoint
        self.pipelines_endpoint = pipelines_endpoint
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.max_rate_limit_waits = max_rate_limit_waits
        self.rate_limiter = RateLimiter(max_requests_per_window, window_seconds)
        self.logger = get_logger(__name__)
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "HubSpot-Deals-ETL/1.0",
            }
        )
        self.last_rate_limit_info: Dict[str, Any] = {}

        self.logger.debug(
            "HubSpot API service initialized",
            extra={
                "operation": "hubspot_api_init",
                "base_url": self.base_url,
                "rate_limit": f"{max_requests_per_window}/{window_seconds}s",
                "max_retries": max_retries,
            },
        )

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "HubSpotAPIService":
        """Build a client from the extraction config (see Config.get_hubspot_config)"""
        return cls(
            base_url=config.get("hubspot_api_base_url", "https://api.hubapi.com"),
            deals_endpoint=config.get("hubspot_deals_endpoint", "/crm/v3/objects/deals"),
            properties_endpoint=config.get(
                "hubspot_deal_properties_endpoint", "/crm/v3/properties/deals"
            ),
            pipelines_endpoint=config.get(
                "hubspot_deal_pipelines_endpoint", "/crm/v3/pipelines/deals"
            ),
            timeout=int(config.get("hubspot_api_timeout", 30)),
            max_requests_per_window=int(config.get("hubspot_rate_limit_max_requests", 150)),
            window_seconds=float(config.get("hubspot_rate_limit_window_seconds", 10)),
            max_retries=int(config.get("hubspot_retry_attempts", 3)),
            backoff_seconds=float(config.get("hubspot_retry_backoff_seconds", 1)),
        )

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #
    @staticmethod
    def _auth_headers(access_token: str) -> Dict[str, str]:
        if not access_token or not access_token.strip():
            raise HubSpotAuthenticationError(
                "HubSpot access token is missing", status_code=401
            )
        return {"Authorization": f"Bearer {access_token.strip()}"}

    @staticmethod
    def mask_token(access_token: Optional[str]) -> str:
        """Return a log-safe representation of a token"""
        if not access_token:
            return "<none>"
        return f"{access_token[:7]}...{access_token[-4:]}" if len(access_token) > 12 else "***"

    # ------------------------------------------------------------------ #
    # Core request handling
    # ------------------------------------------------------------------ #
    def _request(
        self,
        method: str,
        path: str,
        access_token: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        operation: str = "hubspot_request",
    ) -> Dict[str, Any]:
        """
        Perform a rate-limited request with retries and return the parsed JSON.
        Raises a HubSpotAPIError subclass on failure.
        """
        url = f"{self.base_url}{path}"
        headers = self._auth_headers(access_token)
        # Network errors and 5xx use up the retries; waiting out a 429 does not,
        # it has its own (larger) limit so a stuck rate limit cannot loop forever
        retries = 0
        rate_limit_waits = 0

        while True:
            attempt = retries + rate_limit_waits + 1
            waited = self.rate_limiter.acquire()
            if waited > 0:
                self.logger.debug(
                    "Client-side rate limiter delayed request",
                    extra={"operation": operation, "waited_seconds": round(waited, 3)},
                )

            started = time.monotonic()
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    timeout=self.timeout,
                )
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if retries < self.max_retries:
                    retries += 1
                    delay = self._backoff_delay(retries)
                    self.logger.warning(
                        "Network error calling HubSpot, retrying",
                        extra={
                            "operation": operation,
                            "attempt": attempt,
                            "retry_in_seconds": delay,
                            "error": str(e),
                        },
                    )
                    time.sleep(delay)
                    continue
                raise HubSpotServerError(
                    f"Could not reach HubSpot API after {attempt} attempts: {e}"
                ) from e

            duration_ms = round((time.monotonic() - started) * 1000, 2)
            self._record_rate_limit_headers(response)
            log_api_call(
                self.logger,
                operation,
                method=method,
                status_code=response.status_code,
                duration_ms=duration_ms,
                attempt=attempt,
            )

            if response.status_code < 400:
                self._respect_remaining_quota()
                if response.status_code == 204 or not response.content:
                    return {}
                return response.json()

            error = self._build_error(response)

            if response.status_code == 429:
                if error.category == "DAILY" or "daily" in error.message.lower():
                    # Waiting a few seconds will not help with the daily quota
                    raise error
                if rate_limit_waits < self.max_rate_limit_waits:
                    rate_limit_waits += 1
                    delay = self._retry_after_seconds(response, rate_limit_waits)
                    self.logger.warning(
                        "HubSpot rate limit hit, backing off",
                        extra={
                            "operation": operation,
                            "attempt": attempt,
                            "rate_limit_waits": rate_limit_waits,
                            "retry_in_seconds": delay,
                            "policy": error.category,
                        },
                    )
                    time.sleep(delay)
                    continue
                raise error

            if response.status_code >= 500 and retries < self.max_retries:
                retries += 1
                delay = self._backoff_delay(retries)
                self.logger.warning(
                    "HubSpot server error, retrying",
                    extra={
                        "operation": operation,
                        "attempt": attempt,
                        "status_code": response.status_code,
                        "retry_in_seconds": delay,
                        "correlation_id": error.correlation_id,
                    },
                )
                time.sleep(delay)
                continue

            self.logger.error(
                "HubSpot API request failed",
                extra={
                    "operation": operation,
                    "status_code": response.status_code,
                    "error_type": type(error).__name__,
                    "hubspot_message": error.message,
                    "correlation_id": error.correlation_id,
                },
            )
            raise error

    def _backoff_delay(self, attempt: int) -> float:
        return self.backoff_seconds * (2 ** (attempt - 1))

    def _retry_after_seconds(self, response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 0.5)
            except ValueError:
                pass
        interval_ms = response.headers.get("X-HubSpot-RateLimit-Interval-Milliseconds")
        if interval_ms:
            try:
                return max(float(interval_ms) / 1000.0, 1.0)
            except ValueError:
                pass
        # HubSpot's burst window is 10 s, so waiting longer than that never helps
        return min(self._backoff_delay(attempt), 10.0)

    def _record_rate_limit_headers(self, response: requests.Response) -> None:
        header_map = {
            "daily_limit": "X-HubSpot-RateLimit-Daily",
            "daily_remaining": "X-HubSpot-RateLimit-Daily-Remaining",
            "interval_milliseconds": "X-HubSpot-RateLimit-Interval-Milliseconds",
            "interval_max": "X-HubSpot-RateLimit-Max",
            "interval_remaining": "X-HubSpot-RateLimit-Remaining",
        }
        info = {
            key: response.headers.get(header)
            for key, header in header_map.items()
            if response.headers.get(header) is not None
        }
        if info:
            info["observed_at"] = datetime.now(timezone.utc).isoformat()
            self.last_rate_limit_info = info

    def _respect_remaining_quota(self) -> None:
        """
        If HubSpot reports the current window is almost exhausted (for example
        when another app shares the account), wait for the window to roll over
        instead of provoking a 429.
        """
        remaining = self.last_rate_limit_info.get("interval_remaining")
        interval_ms = self.last_rate_limit_info.get("interval_milliseconds")
        try:
            if remaining is not None and int(remaining) <= 1:
                pause = float(interval_ms) / 1000.0 if interval_ms else 10.0
                self.logger.info(
                    "HubSpot burst quota nearly exhausted, pausing",
                    extra={"operation": "rate_limit_guard", "pause_seconds": pause},
                )
                time.sleep(pause)
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _build_error(response: requests.Response) -> HubSpotAPIError:
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = {}
        message = body.get("message") or response.reason or f"HTTP {status}"
        category = body.get("policyName") or body.get("category")
        correlation_id = body.get("correlationId")

        if status == 401:
            return HubSpotAuthenticationError(
                f"HubSpot rejected the access token (401): {message}",
                status, category, correlation_id,
            )
        if status == 403:
            return HubSpotPermissionError(
                "HubSpot access token is missing a required scope - deal "
                f"extraction needs crm.objects.deals.read (403): {message}",
                status, category, correlation_id,
            )
        if status == 404:
            return HubSpotNotFoundError(
                f"HubSpot resource not found (404): {message}",
                status, category, correlation_id,
            )
        if status == 429:
            return HubSpotRateLimitError(
                f"HubSpot rate limit exceeded (429): {message}",
                status, category, correlation_id,
            )
        if status >= 500:
            return HubSpotServerError(
                f"HubSpot server error ({status}): {message}",
                status, category, correlation_id,
            )
        return HubSpotAPIError(
            f"HubSpot API request failed ({status}): {message}",
            status, category, correlation_id,
        )

    # ------------------------------------------------------------------ #
    # Deals
    # ------------------------------------------------------------------ #
    def get_deals(
        self,
        access_token: str,
        limit: int = HUBSPOT_MAX_PAGE_SIZE,
        after: Optional[str] = None,
        properties: Optional[Iterable[str]] = None,
        archived: bool = False,
    ) -> Dict[str, Any]:
        """
        Fetch one page of deals: GET /crm/v3/objects/deals

        Returns HubSpot's raw payload ``{"results": [...], "paging": {...}}``.
        The cursor for the next page is ``paging.next.after``; it is absent on
        the last page.
        """
        page_size = max(1, min(int(limit), HUBSPOT_MAX_PAGE_SIZE))
        requested = list(dict.fromkeys(properties or DEFAULT_DEAL_PROPERTIES))
        params: Dict[str, Any] = {
            "limit": page_size,
            "properties": ",".join(requested),
            "archived": "true" if archived else "false",
        }
        if after:
            params["after"] = after

        self.logger.debug(
            "Fetching deals page",
            extra={
                "operation": "get_deals",
                "limit": page_size,
                "has_cursor": after is not None,
                "property_count": len(requested),
                "archived": archived,
            },
        )
        data = self._request(
            "GET", self.deals_endpoint, access_token, params=params, operation="hubspot_get_deals"
        )
        self.logger.info(
            "Deals page retrieved",
            extra={
                "operation": "get_deals",
                "result_count": len(data.get("results", [])),
                "has_more": self.get_next_cursor(data) is not None,
            },
        )
        return data

    @staticmethod
    def get_next_cursor(page: Dict[str, Any]) -> Optional[str]:
        """Extract ``paging.next.after`` from a page, or None on the last page"""
        return ((page or {}).get("paging") or {}).get("next", {}).get("after")

    def iterate_deals(
        self,
        access_token: str,
        page_size: int = HUBSPOT_MAX_PAGE_SIZE,
        properties: Optional[Iterable[str]] = None,
        archived: bool = False,
        after: Optional[str] = None,
    ):
        """Yield every deal, following the pagination cursor until exhausted"""
        cursor = after
        while True:
            page = self.get_deals(
                access_token, limit=page_size, after=cursor, properties=properties, archived=archived
            )
            yield from page.get("results", [])
            cursor = self.get_next_cursor(page)
            if not cursor:
                return

    def get_deal(
        self, access_token: str, deal_id: str, properties: Optional[Iterable[str]] = None
    ) -> Dict[str, Any]:
        """Fetch a single deal: GET /crm/v3/objects/deals/{dealId}"""
        params = {"properties": ",".join(properties or DEFAULT_DEAL_PROPERTIES)}
        return self._request(
            "GET",
            f"{self.deals_endpoint}/{deal_id}",
            access_token,
            params=params,
            operation="hubspot_get_deal",
        )

    def create_deal(self, access_token: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        """Create a deal (requires crm.objects.deals.write); used for test data setup"""
        return self._request(
            "POST",
            self.deals_endpoint,
            access_token,
            json_body={"properties": properties},
            operation="hubspot_create_deal",
        )

    def get_deal_properties(self, access_token: str) -> List[Dict[str, Any]]:
        """List every deal property definition: GET /crm/v3/properties/deals"""
        data = self._request(
            "GET", self.properties_endpoint, access_token, operation="hubspot_get_deal_properties"
        )
        return data.get("results", [])

    def get_deal_pipelines(self, access_token: str) -> List[Dict[str, Any]]:
        """List deal pipelines and their stages: GET /crm/v3/pipelines/deals"""
        data = self._request(
            "GET", self.pipelines_endpoint, access_token, operation="hubspot_get_deal_pipelines"
        )
        return data.get("results", [])

    # ------------------------------------------------------------------ #
    # Credential validation
    # ------------------------------------------------------------------ #
    def validate_credentials(self, access_token: str) -> Dict[str, Any]:
        """
        Check that the token is accepted and has deal read access by requesting
        a single deal. Never raises; returns a result dictionary instead.
        """
        result: Dict[str, Any] = {
            "valid": False,
            "has_deals_read_scope": False,
            "status_code": None,
            "message": None,
            "rate_limit": None,
        }
        self.logger.info(
            "Validating HubSpot credentials",
            extra={"operation": "validate_credentials", "token": self.mask_token(access_token)},
        )
        try:
            self.get_deals(access_token, limit=1, properties=["dealname"])
            result.update(
                valid=True,
                has_deals_read_scope=True,
                status_code=200,
                message="Access token is valid and can read deals",
            )
        except HubSpotPermissionError as e:
            # The token authenticated but the private app is missing a scope
            result.update(valid=True, status_code=e.status_code, message=e.message)
        except HubSpotAPIError as e:
            result.update(status_code=e.status_code, message=e.message)

        result["rate_limit"] = self.last_rate_limit_info or None
        log_level = self.logger.info if result["has_deals_read_scope"] else self.logger.warning
        log_level(
            "HubSpot credential validation finished",
            extra={
                "operation": "validate_credentials",
                "valid": result["valid"],
                "has_deals_read_scope": result["has_deals_read_scope"],
                "status_code": result["status_code"],
            },
        )
        return result

    def get_api_usage(self) -> Optional[Dict[str, Any]]:
        """Rate-limit headers observed on the most recent response"""
        return self.last_rate_limit_info or None
