# Local HubSpot CRM v3 API mock (deals) - see mock_hubspot/README.md
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir Flask==3.0.0 Werkzeug==3.1.9

COPY mock_hubspot /app/mock_hubspot

ENV PYTHONUNBUFFERED=1 \
    MOCK_HUBSPOT_PORT=5299 \
    MOCK_HUBSPOT_DATA_FILE=/data/hubspot_mock_state.json

EXPOSE 5299

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5299/__mock/health')" || exit 1

CMD ["python", "-m", "mock_hubspot.server"]
