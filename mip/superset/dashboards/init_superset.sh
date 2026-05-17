#!/bin/bash
# ============================================================
# Superset Bootstrap Script – runs in superset-init container
# Creates admin user, registers ministry_db, imports dashboards
# ============================================================

set -euo pipefail

SUPERSET_URL="http://superset:8088"
ADMIN_USER="${SUPERSET_ADMIN_USERNAME:-admin}"
ADMIN_PASS="${SUPERSET_ADMIN_PASSWORD:-admin}"
ADMIN_EMAIL="${SUPERSET_ADMIN_EMAIL:-admin@ministry.org}"
ADMIN_FIRSTNAME="${SUPERSET_ADMIN_FIRSTNAME:-MIP}"
ADMIN_LASTNAME="${SUPERSET_ADMIN_LASTNAME:-Admin}"
DB_USER="${POSTGRES_USER:-ministry}"
DB_PASS="${POSTGRES_PASSWORD:-ministry}"
DB_HOST="${DATABASE_HOST:-db}"
DB_NAME="${POSTGRES_DB:-ministry_db}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ── Wait for Superset to be ready ────────────────────────────────
log "Waiting for Superset to be healthy..."
for i in $(seq 1 60); do
    if curl -sf "${SUPERSET_URL}/health" > /dev/null 2>&1; then
        log "Superset is healthy."
        break
    fi
    log "Attempt $i/60: Superset not ready yet..."
    sleep 5
done

# ── Create admin user ─────────────────────────────────────────────
log "Creating Superset admin user..."
superset fab create-admin \
    --username "${ADMIN_USER}" \
    --firstname "${ADMIN_FIRSTNAME}" \
    --lastname  "${ADMIN_LASTNAME}" \
    --email     "${ADMIN_EMAIL}" \
    --password  "${ADMIN_PASS}" || log "Admin user may already exist; continuing."

# ── Initialize Superset metadata ─────────────────────────────────
log "Initializing Superset metadata..."
superset init

# ── Get auth token via REST API ───────────────────────────────────
log "Authenticating with Superset API..."
AUTH_RESPONSE=$(curl -sf -X POST \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${ADMIN_USER}\",\"password\":\"${ADMIN_PASS}\",\"provider\":\"db\"}" \
    "${SUPERSET_URL}/api/v1/security/login")

ACCESS_TOKEN=$(echo "${AUTH_RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
log "Authenticated successfully."

AUTH_HEADER="Authorization: Bearer ${ACCESS_TOKEN}"
CONTENT_JSON="Content-Type: application/json"

# ── Helper: CSRF token ────────────────────────────────────────────
get_csrf_token() {
    curl -sf -H "${AUTH_HEADER}" "${SUPERSET_URL}/api/v1/security/csrf_token/" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['result'])"
}

CSRF_TOKEN=$(get_csrf_token)

# ── Register ministry_db database ────────────────────────────────
log "Registering ministry_db database connection..."
DB_PAYLOAD=$(python3 -c "
import json
payload = {
    'database_name': 'ministry_db',
    'engine': 'postgresql',
    'sqlalchemy_uri': 'postgresql+psycopg2://${DB_USER}:${DB_PASS}@${DB_HOST}:5432/${DB_NAME}',
    'expose_in_sqllab': True,
    'allow_run_async': True,
    'allow_ctas': False,
    'allow_cvas': False,
    'allow_dml': False,
    'allow_file_upload': False,
    'extra': json.dumps({'engine_params': {'connect_args': {}}}),
    'is_managed_externally': False
}
print(json.dumps(payload))
")

DB_CHECK=$(curl -sf -H "${AUTH_HEADER}" "${SUPERSET_URL}/api/v1/database/?q=(filters:!((col:database_name,opr:DatabaseIsNull,val:false)))" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(any(x['database_name']=='ministry_db' for x in d.get('result',[])))") || DB_CHECK="False"

if [ "${DB_CHECK}" = "False" ]; then
    DB_RESULT=$(curl -sf -X POST \
        -H "${AUTH_HEADER}" \
        -H "${CONTENT_JSON}" \
        -H "X-CSRFToken: ${CSRF_TOKEN}" \
        -d "${DB_PAYLOAD}" \
        "${SUPERSET_URL}/api/v1/database/")
    DB_ID=$(echo "${DB_RESULT}" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null || echo "")
    log "Database registered with ID: ${DB_ID}"
else
    DB_ID=$(curl -sf -H "${AUTH_HEADER}" "${SUPERSET_URL}/api/v1/database/" \
        | python3 -c "import sys,json; r=json.load(sys.stdin)['result']; print(next(x['id'] for x in r if x['database_name']=='ministry_db'))")
    log "Database already registered with ID: ${DB_ID}"
fi

# ── Create datasets (physical tables) ─────────────────────────────
create_dataset() {
    local table_name="$1"
    local schema="${2:-public}"

    # Check if exists
    local exists
    exists=$(curl -sf -H "${AUTH_HEADER}" \
        "${SUPERSET_URL}/api/v1/dataset/?q=(filters:!((col:table_name,opr:DatasetIsNullOrEmpty,val:'')))" \
        | python3 -c "import sys,json; r=json.load(sys.stdin).get('result',[]); print(any(x['table_name']=='${table_name}' for x in r))" 2>/dev/null || echo "False")

    if [ "${exists}" = "False" ]; then
        CSRF_TOKEN=$(get_csrf_token)
        curl -sf -X POST \
            -H "${AUTH_HEADER}" \
            -H "${CONTENT_JSON}" \
            -H "X-CSRFToken: ${CSRF_TOKEN}" \
            -d "{\"database\":${DB_ID},\"schema\":\"${schema}\",\"table_name\":\"${table_name}\"}" \
            "${SUPERSET_URL}/api/v1/dataset/" > /dev/null
        log "Dataset created: ${table_name}"
    else
        log "Dataset already exists: ${table_name}"
    fi
}

# Create all MIP datasets
for table in attendance members activities outreaches follow_ups finance donations goals \
             giving_categories user_teams v_attendance_summary v_finance_balance \
             v_donation_summary v_weekly_attendance v_followup_performance; do
    create_dataset "${table}"
done

# ── Import Dashboard JSON files ───────────────────────────────────
DASHBOARD_DIR="/app/dashboards"
for dashboard_file in "${DASHBOARD_DIR}"/export_*.json; do
    if [ -f "${dashboard_file}" ]; then
        CSRF_TOKEN=$(get_csrf_token)
        dashboard_name=$(basename "${dashboard_file}" .json)
        log "Importing dashboard: ${dashboard_name}"
        curl -sf -X POST \
            -H "${AUTH_HEADER}" \
            -H "X-CSRFToken: ${CSRF_TOKEN}" \
            -F "formData=@${dashboard_file};type=application/json" \
            -F "override:true" \
            "${SUPERSET_URL}/api/v1/dashboard/import/" > /dev/null && \
            log "Imported: ${dashboard_name}" || \
            log "Warning: Failed to import ${dashboard_name} (may already exist)"
    fi
done

# ── Configure RLS rules via Python (superset-internal) ───────────
log "Configuring Row-Level Security rules..."
python3 << 'PYEOF'
import os
import sys

# RLS configuration using Superset's internal Flask app context
try:
    from superset import create_app
    from superset.extensions import db
    from superset.connectors.sqla.models import RowLevelSecurityFilter

    app = create_app()
    with app.app_context():
        # Get datasets by name
        from superset.connectors.sqla.models import SqlaTable
        tables_to_secure = {
            'attendance': (
                "team_owner IN (SELECT team_id FROM user_teams WHERE user_email = '{{ current_username() }}')"
            ),
            'outreaches': (
                "activity_code IN ("
                "SELECT activity_code FROM activities WHERE team_owner IN ("
                "SELECT team_id FROM user_teams WHERE user_email = '{{ current_username() }}'))"
            ),
            'follow_ups': (
                "assigned_to IN ("
                "SELECT member_id FROM members WHERE email = '{{ current_username() }}')"
            ),
        }

        for table_name, filter_clause in tables_to_secure.items():
            tbl = db.session.query(SqlaTable).filter_by(table_name=table_name).first()
            if tbl:
                # Check if RLS rule already exists
                existing = db.session.query(RowLevelSecurityFilter).filter_by(
                    filter_type='Regular',
                    clause=filter_clause,
                ).first()
                if not existing:
                    rls = RowLevelSecurityFilter(
                        name=f'RLS_{table_name}_team',
                        filter_type='Regular',
                        clause=filter_clause,
                        group_key=None,
                        roles=[],
                        tables=[tbl],
                    )
                    db.session.add(rls)
                    db.session.commit()
                    print(f"RLS rule created for: {table_name}")
                else:
                    print(f"RLS rule already exists for: {table_name}")
            else:
                print(f"Warning: table {table_name} not found in Superset datasets")
except Exception as e:
    print(f"RLS setup warning (non-fatal): {e}")
    sys.exit(0)
PYEOF

log "============================================================"
log "Superset initialization COMPLETE."
log "  URL:       http://superset:8088"
log "  Admin:     ${ADMIN_EMAIL}"
log "  Dashboards imported: $(ls /app/dashboards/export_*.json 2>/dev/null | wc -l)"
log "============================================================"
