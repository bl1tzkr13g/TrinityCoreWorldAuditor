# Packages the official MariaDB/mcp project without changing its application code.
FROM python:3.11-slim AS builder
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates build-essential && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
RUN git clone --depth 1 https://github.com/MariaDB/mcp.git .
RUN uv sync --no-dev
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
ENV PATH="/app/.venv/bin:${PATH}"
EXPOSE 9001
CMD ["python", "src/server.py", "--transport", "http", "--host", "0.0.0.0", "--port", "9001", "--path", "/mcp"]
