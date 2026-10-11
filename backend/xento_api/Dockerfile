# Build context: backend/ (see deploy/compose.yaml)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY xento_api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY xento_api ./xento_api
RUN useradd --system --uid 10001 api
USER api
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "xento_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers"]
