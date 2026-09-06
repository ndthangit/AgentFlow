#!/bin/bash
set -euo pipefail

REALM_FILE=/opt/keycloak/data/import/agentflow-realm.json

echo "Importing AgentFlow realm configuration from ${REALM_FILE}"
/opt/keycloak/bin/kc.sh import \
  --file "${REALM_FILE}" \
  --override true

exec /opt/keycloak/bin/kc.sh start-dev
