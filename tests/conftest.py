"""
Shared pytest configuration.

Seeded-data tests (tests/test_seeded_api.py) run the Flask app in-process
against a dedicated PostgreSQL database, by default `hubspot_deals_test` on
localhost:5432 (the docker-compose `postgres_dev` container). The database is
dropped and recreated once per test session and never touches the development
database. If PostgreSQL is unreachable, those tests are skipped.

Override the connection with TEST_DB_HOST, TEST_DB_PORT, TEST_DB_USER,
TEST_DB_PASSWORD and TEST_DB_NAME.
"""
import os

TEST_DB = {
    "host": os.environ.get("TEST_DB_HOST", "localhost"),
    "port": os.environ.get("TEST_DB_PORT", "5432"),
    "user": os.environ.get("TEST_DB_USER", "postgres"),
    "password": os.environ.get("TEST_DB_PASSWORD", "password123"),
    "name": os.environ.get("TEST_DB_NAME", "hubspot_deals_test"),
}

# config.py reads the environment at import time, so this must run before any
# application module is imported. DB_NAME is forced (not setdefault) so the
# tests can never write into the development database.
os.environ.update({
    "FLASK_ENV": "testing",
    "LOKI_ENABLED": "false",
    "LOG_LEVEL": "WARNING",
    "CONFIG_PASSWORD": "test-config-password",
    "DB_HOST": TEST_DB["host"],
    "DB_PORT": TEST_DB["port"],
    "DB_USER": TEST_DB["user"],
    "DB_PASSWORD": TEST_DB["password"],
    "DB_NAME": TEST_DB["name"],
    "DESTINATION__POSTGRES__CREDENTIALS__HOST": TEST_DB["host"],
    "DESTINATION__POSTGRES__CREDENTIALS__PORT": TEST_DB["port"],
    "DESTINATION__POSTGRES__CREDENTIALS__USERNAME": TEST_DB["user"],
    "DESTINATION__POSTGRES__CREDENTIALS__PASSWORD": TEST_DB["password"],
    "DESTINATION__POSTGRES__CREDENTIALS__DATABASE": TEST_DB["name"],
})
