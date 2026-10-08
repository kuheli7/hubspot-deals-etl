"""
DLT data source for HubSpot deals.

Extraction runs in batches of ``checkpoint_interval`` pages. Each batch is one
``pipeline.run`` call, and the extraction service commits a checkpoint only
after that batch has been loaded into PostgreSQL. A checkpoint therefore never
points past data that is not yet in the database, so a scan that is paused,
cancelled, or interrupted by a crash resumes from the last committed cursor
without losing or skipping pages. Deals are merged on ``id``, so re-reading
a page after a crash cannot create duplicates.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterator, List, Optional

import dlt

from loki_logger import get_logger, log_business_event
from .hubspot_api_service import (
    DEFAULT_DEAL_PROPERTIES,
    HUBSPOT_MAX_PAGE_SIZE,
    HubSpotAPIService,
)

SOURCE_SERVICE = "hubspot_deals"
DEALS_TABLE = "deals"
MAX_PAGES_PER_SCAN = 10000  # safety stop: 1,000,000 deals at 100 per page

# HubSpot returns every property value as a string; these sets drive the
# conversion to the PostgreSQL types documented in docs/database-schema.md
DECIMAL_PROPERTIES = {
    "amount",
    "amount_in_home_currency",
    "hs_forecast_amount",
    "hs_projected_amount",
    "hs_closed_amount",
}
FLOAT_PROPERTIES = {"hs_deal_stage_probability"}
INTEGER_PROPERTIES = {
    "hs_object_id",
    "days_to_close",
    "num_associated_contacts",
    "num_contacted_notes",
}
BOOLEAN_PROPERTIES = {"hs_is_closed", "hs_is_closed_won"}
TIMESTAMP_PROPERTIES = {
    "closedate",
    "createdate",
    "hs_lastmodifieddate",
    "notes_last_updated",
}

def _money() -> Dict[str, Any]:
    # A new dict per column: dlt writes the column name into each hint dict,
    # so sharing one dict between columns would make them overwrite each other
    return {"data_type": "decimal", "precision": 18, "scale": 2}


# dlt column hints -> PostgreSQL column types
DEAL_COLUMN_HINTS: Dict[str, Dict[str, Any]] = {
    "id": {"data_type": "text", "nullable": False, "unique": True},
    "dealname": {"data_type": "text"},
    "amount": _money(),
    "dealstage": {"data_type": "text"},
    "pipeline": {"data_type": "text"},
    "dealtype": {"data_type": "text"},
    "closedate": {"data_type": "timestamp"},
    "createdate": {"data_type": "timestamp"},
    "hs_lastmodifieddate": {"data_type": "timestamp"},
    "description": {"data_type": "text"},
    "hubspot_owner_id": {"data_type": "text"},
    "hs_object_id": {"data_type": "bigint"},
    "hs_priority": {"data_type": "text"},
    "hs_deal_stage_probability": {"data_type": "double"},
    "hs_forecast_amount": _money(),
    "hs_projected_amount": _money(),
    "amount_in_home_currency": _money(),
    "deal_currency_code": {"data_type": "text"},
    "hs_is_closed": {"data_type": "bool"},
    "hs_is_closed_won": {"data_type": "bool"},
    "days_to_close": {"data_type": "bigint"},
    "hs_closed_amount": _money(),
    "closed_lost_reason": {"data_type": "text"},
    "closed_won_reason": {"data_type": "text"},
    "hs_next_step": {"data_type": "text"},
    "hs_analytics_source": {"data_type": "text"},
    "num_associated_contacts": {"data_type": "bigint"},
    "num_contacted_notes": {"data_type": "bigint"},
    "notes_last_updated": {"data_type": "timestamp"},
    "archived": {"data_type": "bool"},
    "created_at": {"data_type": "timestamp"},
    "updated_at": {"data_type": "timestamp"},
    "archived_at": {"data_type": "timestamp"},
    # ETL metadata
    "_extracted_at": {"data_type": "timestamp", "nullable": False},
    "_scan_id": {"data_type": "text", "nullable": False},
    "_tenant_id": {"data_type": "text", "nullable": False},
    "_page_number": {"data_type": "bigint"},
    "_source_service": {"data_type": "text"},
}


@dataclass
class ExtractionProgress:
    """Pagination state shared between the DLT resource and the extraction service"""

    cursor: Optional[str] = None
    page_number: int = 0
    records_processed: int = 0
    finished: bool = False
    stop_reason: Optional[str] = None  # "cancelled" | "paused" | "page_limit"
    batch_start_cursor: Optional[str] = None
    batch_start_page: int = 0
    batches_completed: int = 0
    resumed_from_page: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_checkpoint(cls, checkpoint: Optional[Dict[str, Any]]) -> "ExtractionProgress":
        """Rebuild progress from the latest committed JobCheckpoint (as dict)"""
        if not checkpoint or not checkpoint.get("cursor"):
            return cls()
        page = int(checkpoint.get("pageNumber") or 0)
        return cls(
            cursor=checkpoint.get("cursor"),
            page_number=page,
            records_processed=int(checkpoint.get("recordsProcessed") or 0),
            resumed_from_page=page,
        )

    def start_batch(self) -> None:
        self.batch_start_cursor = self.cursor
        self.batch_start_page = self.page_number
        self.stop_reason = None

    def to_checkpoint(self, phase: str, page_size: int, **extra: Any) -> Dict[str, Any]:
        """Build kwargs for JobService.save_checkpoint / JobCheckpoint.create_checkpoint"""
        return {
            "phase": phase,
            "records_processed": self.records_processed,
            "cursor": None if self.finished else self.cursor,
            "page_number": self.page_number,
            "batch_size": page_size,
            "checkpoint_data": {
                "service": SOURCE_SERVICE,
                "pages_processed": self.page_number,
                "batches_completed": self.batches_completed,
                "batch_start_page": self.batch_start_page,
                "finished": self.finished,
                "stop_reason": self.stop_reason,
                "resumed_from_page": self.resumed_from_page,
                "committed_at": datetime.now(timezone.utc).isoformat(),
                **extra,
            },
        }


# ---------------------------------------------------------------------- #
# Type conversion helpers
# ---------------------------------------------------------------------- #
def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def parse_hubspot_datetime(value: Any) -> Optional[datetime]:
    """
    Parse HubSpot datetime values: ISO-8601 strings (``2025-01-15T10:30:00.000Z``),
    plain dates (``2025-01-15``) or epoch milliseconds (``1736937000000``).
    Always returns a timezone-aware UTC datetime.
    """
    if _blank(value):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    try:
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000.0, tz=timezone.utc)
        if len(text) == 10:  # YYYY-MM-DD
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError):
        return None


def parse_decimal(value: Any) -> Optional[Decimal]:
    if _blank(value):
        return None
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def parse_float(value: Any) -> Optional[float]:
    if _blank(value):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def parse_int(value: Any) -> Optional[int]:
    if _blank(value):
        return None
    try:
        return int(Decimal(str(value).strip()))
    except (InvalidOperation, ValueError):
        return None


def parse_bool(value: Any) -> Optional[bool]:
    if _blank(value):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


def convert_property(name: str, value: Any) -> Any:
    """Convert a single HubSpot property value to its schema type"""
    if name in DECIMAL_PROPERTIES:
        return parse_decimal(value)
    if name in FLOAT_PROPERTIES:
        return parse_float(value)
    if name in INTEGER_PROPERTIES:
        return parse_int(value)
    if name in BOOLEAN_PROPERTIES:
        return parse_bool(value)
    if name in TIMESTAMP_PROPERTIES:
        return parse_hubspot_datetime(value)
    # enumerations, strings and any extra requested property stay text
    return None if _blank(value) else str(value)


def transform_deal(
    deal: Dict[str, Any],
    scan_id: str,
    tenant_id: str,
    page_number: int,
    extracted_at: Optional[datetime] = None,
    requested_properties: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Flatten a HubSpot deal object into a typed row for the ``deals`` table.

    Input:  {"id": "123", "properties": {"amount": "5000", ...},
             "createdAt": "...", "updatedAt": "...", "archived": false}
    Output: {"id": "123", "amount": Decimal("5000"), ..., "_scan_id": ..., ...}
    """
    properties = deal.get("properties") or {}
    row: Dict[str, Any] = {"id": str(deal.get("id"))}

    # Every requested property gets a key, even when HubSpot omits it, so the
    # table shape is stable across pages
    for name in requested_properties or DEFAULT_DEAL_PROPERTIES:
        row[name] = convert_property(name, properties.get(name))
    # Keep anything else HubSpot returned
    for name, value in properties.items():
        if name not in row:
            row[name] = convert_property(name, value)

    row.update(
        {
            "archived": bool(deal.get("archived", False)),
            "created_at": parse_hubspot_datetime(deal.get("createdAt")),
            "updated_at": parse_hubspot_datetime(deal.get("updatedAt")),
            "archived_at": parse_hubspot_datetime(deal.get("archivedAt")),
            "_extracted_at": extracted_at or datetime.now(timezone.utc),
            "_scan_id": scan_id,
            "_tenant_id": tenant_id,
            "_page_number": page_number,
            "_source_service": SOURCE_SERVICE,
        }
    )
    return row


def resolve_properties(filters: Dict[str, Any]) -> List[str]:
    """Default deal properties plus any extra ones requested in the scan filters"""
    extra = [p.strip() for p in (filters.get("properties") or []) if p and p.strip()]
    return list(dict.fromkeys(DEFAULT_DEAL_PROPERTIES + extra))


# ---------------------------------------------------------------------- #
# DLT source
# ---------------------------------------------------------------------- #
def create_data_source(
    job_config: Dict[str, Any],
    auth_config: Dict[str, Any],
    filters: Dict[str, Any],
    api_service: HubSpotAPIService,
    progress: ExtractionProgress,
    checkpoint_interval: int = 10,
    page_size: int = HUBSPOT_MAX_PAGE_SIZE,
    check_cancel_callback: Optional[Callable[[str], bool]] = None,
    check_pause_callback: Optional[Callable[[str], bool]] = None,
    page_delay_seconds: float = 0,
):
    """
    Create the DLT resources for one extraction batch.

    The ``deals`` resource reads at most ``checkpoint_interval`` pages, starting
    at ``progress.cursor``, and updates ``progress`` as it goes. The caller runs
    the pipeline, commits ``progress`` as a checkpoint, and calls this again
    until ``progress.finished`` or ``progress.stop_reason`` is set.
    """
    logger = get_logger(__name__)

    access_token = (auth_config or {}).get("accessToken")
    if not access_token:
        raise ValueError("No access token found in auth configuration")

    tenant_id = job_config.get("organizationId")
    if not tenant_id:
        raise ValueError("No organization ID found in job configuration")

    scan_id = filters.get("scan_id") or job_config.get("scanId") or "unknown"
    properties = resolve_properties(filters)
    archived = bool(filters.get("archived", False))
    page_size = max(1, min(int(filters.get("pageSize") or page_size), HUBSPOT_MAX_PAGE_SIZE))
    checkpoint_interval = max(1, int(filters.get("checkpointInterval") or checkpoint_interval))

    @dlt.resource(
        name=DEALS_TABLE,
        write_disposition="merge",
        primary_key="id",
        columns=DEAL_COLUMN_HINTS,
    )
    def deals() -> Iterator[List[Dict[str, Any]]]:
        """Yield one list of transformed deals per HubSpot page"""
        progress.start_batch()
        pages_in_batch = 0

        logger.info(
            "Starting deals extraction batch",
            extra={
                "operation": "data_extraction",
                "job_id": scan_id,
                "tenant_id": tenant_id,
                "start_page": progress.page_number + 1,
                "has_cursor": progress.cursor is not None,
                "page_size": page_size,
                "checkpoint_interval": checkpoint_interval,
            },
        )

        while True:
            if check_cancel_callback and check_cancel_callback(scan_id):
                progress.stop_reason = "cancelled"
                logger.info(
                    "Extraction cancelled by user",
                    extra={"operation": "data_extraction", "job_id": scan_id,
                           "page_number": progress.page_number},
                )
                return

            if check_pause_callback and check_pause_callback(scan_id):
                progress.stop_reason = "paused"
                logger.info(
                    "Extraction paused by user",
                    extra={"operation": "data_extraction", "job_id": scan_id,
                           "page_number": progress.page_number},
                )
                return

            if pages_in_batch >= checkpoint_interval:
                # Batch boundary: hand control back so this batch is loaded and
                # a checkpoint is committed before more pages are read
                return

            if progress.page_number >= MAX_PAGES_PER_SCAN:
                progress.stop_reason = "page_limit"
                logger.warning(
                    "Safety page limit reached, stopping extraction",
                    extra={"operation": "data_extraction", "job_id": scan_id,
                           "max_pages": MAX_PAGES_PER_SCAN},
                )
                return

            if page_delay_seconds and progress.page_number > 0:
                time.sleep(page_delay_seconds)

            page = api_service.get_deals(
                access_token=access_token,
                limit=page_size,
                after=progress.cursor,
                properties=properties,
                archived=archived,
            )
            results = page.get("results", [])
            page_number = progress.page_number + 1
            extracted_at = datetime.now(timezone.utc)
            rows = [
                transform_deal(deal, scan_id, tenant_id, page_number, extracted_at, properties)
                for deal in results
            ]

            next_cursor = api_service.get_next_cursor(page)
            progress.page_number = page_number
            progress.records_processed += len(rows)
            progress.cursor = next_cursor
            pages_in_batch += 1

            logger.debug(
                "Deals page processed",
                extra={
                    "operation": "data_extraction",
                    "job_id": scan_id,
                    "page_number": page_number,
                    "page_records": len(rows),
                    "total_records": progress.records_processed,
                    "has_more": next_cursor is not None,
                },
            )

            if rows:
                yield rows

            if not next_cursor:
                progress.finished = True
                logger.info(
                    "All deal pages read",
                    extra={"operation": "data_extraction", "job_id": scan_id,
                           "total_pages": progress.page_number,
                           "total_records": progress.records_processed},
                )
                log_business_event(
                    logger, "deals_extraction_pages_exhausted",
                    job_id=scan_id, total_records=progress.records_processed,
                )
                return

    return [deals]
