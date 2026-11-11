# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VENDORFLOW_DATA_DIR=/app/data
WORKDIR /app
COPY requirements.lock .
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir -r requirements.lock; else pip install --no-cache-dir -r requirements.lock; fi \
    && useradd --uid 10001 --create-home appuser
COPY --chown=appuser:appuser vendorflow ./vendorflow
COPY --chown=appuser:appuser examples ./examples
RUN mkdir -p /app/data && chown appuser:appuser /app/data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["gunicorn", "vendorflow.app:create_app()", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4"]
