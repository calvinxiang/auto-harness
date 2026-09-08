FROM python:3.12-slim AS base
WORKDIR /app
COPY requirements-service.lock .
RUN pip install --no-cache-dir -r requirements-service.lock
FROM docker:27-cli AS docker-cli
FROM base AS worker-deps
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements-harbor.lock .
RUN python -m venv /opt/harbor && /opt/harbor/bin/pip install --no-cache-dir -r requirements-harbor.lock

FROM base AS api
COPY service /app/service
COPY tests /app/tests
COPY test_client.py /app/test_client.py
COPY agent/templates/terminal_bench.py /app/agent/templates/terminal_bench.py
CMD ["uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8000"]

FROM worker-deps AS worker
COPY service /app/service
COPY tests /app/tests
COPY test_client.py /app/test_client.py
COPY agent/templates/terminal_bench.py /app/agent/templates/terminal_bench.py
CMD ["python", "-m", "service.worker"]
