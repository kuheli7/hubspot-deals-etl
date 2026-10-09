from flask import Flask
from flask_cors import CORS
import logging
import os
import threading
import time
from datetime import datetime, timezone

from config import get_config
from api.routes import create_api
from coordinator_auth import verify_request
from loki_logger import configure_app_logging
from models.database import initialize_database, check_database_health
from services.job_service import JobService

_crash_monitor_started = False


def start_crash_monitor(timeout_minutes: int):
    """
    Mark scans whose worker died as crashed - once at startup, then every
    timeout_minutes - so it no longer depends on someone calling
    POST /maintenance/detect-crashed. Only jobs whose heartbeat is older than
    the timeout are touched, so scans still running in another worker are safe.
    """
    global _crash_monitor_started
    if _crash_monitor_started:
        return
    _crash_monitor_started = True

    def run():
        while True:
            try:
                JobService().detect_crashed_jobs(timeout_minutes)
            except Exception as e:
                logging.getLogger(__name__).warning(f"Crash detection failed: {e}")
            time.sleep(timeout_minutes * 60)

    threading.Thread(target=run, name="crash-detection", daemon=True).start()


def create_app(config_name: str = None) -> Flask:
    """Application factory function"""
    
    # Create Flask app
    app = Flask(__name__)
    
    # Load configuration
    config = get_config(config_name)
    config.validate_required_settings()
    app.config.from_object(config)
    
    # Setup CORS
    CORS(app, resources={
        r"/api/*": {
            "origins": ["http://localhost:3000", "http://localhost:8080", "http://localhost:3001"],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"]
        },
        r"/docs/*": {
            "origins": ["http://localhost:3000", "http://localhost:8080", "http://localhost:3001"],
            "methods": ["GET"],
            "allow_headers": ["Content-Type"]
        }
    })
    
    # Setup logging
    setup_logging(app, config)
    # Initialize database tables
    initialize_database()
    
    # Every /api/v1 request must be HMAC-signed with the coordinator key
    @app.before_request
    def require_coordinator_signature():
        return verify_request(config.COORDINATOR_KEY, config.API_PREFIX)

    api = create_api()
    # Initialize Flask-RESTX API
    api.init_app(app)

    # Tests seed jobs in exact states, so they start the monitor themselves
    if not config.TESTING:
        start_crash_monitor(config.get_api_config()['crash_detection_timeout'])
    
    # Root route
    @app.route('/')
    def index():
        return {
            "service": config.APP_TITLE,
            "version": config.APP_VERSION,
            "documentation": config.API_DOCS_PATH,
            "health": "/health",
            "endpoints": {
                "start_scan": "POST /api/v1/scan/start",
                "scan_status": "GET /api/v1/scan/{scan_id}/status",
                "pause_scan": "POST /api/v1/scan/{scan_id}/pause",
                "resume_scan": "POST /api/v1/scan/{scan_id}/resume",
                "cancel_scan": "POST /api/v1/scan/{scan_id}/cancel",
                "remove_scan": "DELETE /api/v1/scan/{scan_id}/remove",
                "list_scans": "GET /api/v1/scan/list",
                "scan_statistics": "GET /api/v1/scan/statistics",
                "result_tables": "GET /api/v1/results/{scan_id}/tables",
                "results": "GET /api/v1/results/{scan_id}/result?tableName=deals",
                "validate_credentials": "POST /api/v1/auth/validate",
                "pipeline_info": "GET /api/v1/pipeline/info",
                "cleanup": "POST /api/v1/maintenance/cleanup",
                "detect_crashed": "POST /api/v1/maintenance/detect-crashed",
                "service_health": "GET /api/v1/health",
                "service_stats": "GET /api/v1/stats"
            }
        }

    # Lightweight liveness/readiness probe used by Docker and load balancers
    @app.route('/health', endpoint='liveness_health')
    def liveness_health():
        db_health = check_database_health()
        healthy = bool(db_health.get('healthy'))
        body = {
            "status": "healthy" if healthy else "unhealthy",
            "service": "hubspot_deals",
            "environment": os.environ.get('FLASK_ENV', 'development'),
            "version": config.APP_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {
                "database": "ok" if healthy else db_health.get('error', 'unavailable')
            }
        }
        return body, (200 if healthy else 503)
    
    return app


def setup_logging(app: Flask, config):
    """Setup application logging"""
    
    # Configure basic logging
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL),
        format=config.LOG_FORMAT
    )
    
    # Setup Loki logging if enabled - ONLY ONCE
    if config.LOKI_ENABLED and not hasattr(app, '_loki_configured'):
        try:
            configure_app_logging(app)
            app._loki_configured = True  # Mark as configured
            app.logger.info("Loki logging enabled")
        except Exception as e:
            app.logger.warning(f"Failed to setup Loki logging: {e}")


# Create app instance
app = create_app()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    app.run(host=host, port=port, debug=debug)
