#!/bin/bash
set -euo pipefail

REALM_FILE=/opt/keycloak/data/import/agentflow-realm.json

echo "Importing AgentFlow realm configuration when it does not already exist"
/opt/keycloak/bin/kc.sh import \
  --file "${REALM_FILE}" \
  --override false

exec /opt/keycloak/bin/kc.sh start-dev
