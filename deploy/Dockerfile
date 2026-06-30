# Hosted MCP server for the Second Brain — serves the brain over HTTP + bearer auth.
# Build context = repo root:  docker build -f deploy/Dockerfile .
FROM python:3.12-slim

WORKDIR /app
COPY deploy/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# the agent code (mcp_http reuses mcp_server's tools; db/content do the DB work)
COPY oracle/agent/ ./agent/
WORKDIR /app/agent

ENV PORT=8000
EXPOSE 8000
# Config comes from real env vars / Fly secrets (DB_DSN, DB_USER, APP_PWD, MCP_AUTH_TOKEN).
CMD ["sh", "-c", "uvicorn mcp_http:app --host 0.0.0.0 --port ${PORT:-8000}"]
