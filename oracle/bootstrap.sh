#!/usr/bin/env bash
# Post-boot setup for the local Oracle 26ai container: apply schema, grant model rights,
# load the ONNX embedding model. Idempotent — safe to re-run. Requires the container
# (ccc-oracle) started via docker-compose, and oracle/models/all_MiniLM_L12_v2.onnx present
# (run ./download-model.sh first).
set -euo pipefail
cd "$(dirname "$0")"

# load passwords from .env (falls back to demo defaults)
set -a; [ -f .env ] && . ./.env; set +a
ORA="${ORACLE_PWD:-CHANGE_ME_SysPwd1}"
APP="${APP_PWD:-CHANGE_ME_AppPwd1}"
DSN="localhost:1521/FREEPDB1"

echo "waiting for Oracle to be healthy..."
until [ "$(docker inspect -f '{{.State.Health.Status}}' ccc-oracle 2>/dev/null || echo none)" = "healthy" ]; do
  sleep 5; printf '.'
done
echo " ready."

echo "applying schema (as CCC)..."
docker exec -i ccc-oracle bash -lc "sqlplus -s CCC/${APP}@${DSN}" <<'SQL'
whenever sqlerror continue
drop view post_dv;
drop view tool_stats;
drop table content_chunks cascade constraints;
drop table media cascade constraints;
drop table posts cascade constraints;
drop table deals cascade constraints;
drop table brands cascade constraints;
drop table platforms cascade constraints;
drop table agent_memory cascade constraints;
drop table semantic_memory cascade constraints;
drop table conversations cascade constraints;
drop table procedural_memory cascade constraints;
drop view wiki_page_dv;
drop table wiki_meta cascade constraints;
drop table page_links cascade constraints;
drop table page_sources cascade constraints;
drop table wiki_pages cascade constraints;
drop table analytics cascade constraints;
@/container-entrypoint-initdb.d/01_content_duality.sql
@/container-entrypoint-initdb.d/02_agent_memory.sql
@/container-entrypoint-initdb.d/03_semantic_memory.sql
@/container-entrypoint-initdb.d/04_content_chunks.sql
@/container-entrypoint-initdb.d/05_conversational_memory.sql
@/container-entrypoint-initdb.d/06_procedural_memory.sql
@/container-entrypoint-initdb.d/07_wiki.sql
@/container-entrypoint-initdb.d/08_analytics.sql
exit
SQL

echo "granting model rights + directory (as SYSTEM)..."
docker exec -i ccc-oracle bash -lc "sqlplus -s system/${ORA}@${DSN}" <<'SQL'
create or replace directory VEC_MODELS as '/models';
grant read on directory VEC_MODELS to CCC;
grant create mining model to CCC;
exit
SQL

echo "loading ONNX embedding model 'MINILM' (as CCC)..."
docker exec -i ccc-oracle bash -lc "sqlplus -s CCC/${APP}@${DSN}" <<'SQL'
set serveroutput on
begin
  begin dbms_vector.drop_onnx_model('MINILM'); exception when others then null; end;
  dbms_vector.load_onnx_model('VEC_MODELS','all_MiniLM_L12_v2.onnx','MINILM',
    json('{"function":"embedding","embeddingOutput":"embedding","input":{"input":["DATA"]}}'));
  dbms_output.put_line('MINILM loaded.');
end;
/
exit
SQL

echo "bootstrap complete."
