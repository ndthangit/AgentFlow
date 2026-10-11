#!/bin/bash
set -euo pipefail

REALM_FILE=/opt/keycloak/data/import/agentflow-realm.json
START_MODE=${KEYCLOAK_START_MODE:-dev}

echo "Importing AgentFlow realm configuration when it does not already exist"
/opt/keycloak/bin/kc.sh import \
  --file "${REALM_FILE}" \
  --override false

if [[ "${START_MODE}" == "dev" ]]; then
  exec /opt/keycloak/bin/kc.sh start-dev
fi

if [[ "${START_MODE}" != "production" ]]; then
  echo "KEYCLOAK_START_MODE must be either dev or production" >&2
  exit 1
fi

exec /opt/keycloak/bin/kc.sh start \
  --http-enabled=true \
  --proxy-headers=xforwarded
