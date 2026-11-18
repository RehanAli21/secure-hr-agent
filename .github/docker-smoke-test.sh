#!/usr/bin/env bash
# Docker check: do the containers from compose.yaml build, start and talk to each other?
# CI runs it on every push (.github/workflows/ci.yml). You can run it too, from the project folder
# in Git Bash:   bash .github/docker-smoke-test.sh
#
# It starts everything EXCEPT the AI model (see compose.ci.yaml), then checks, through the page's
# own address (http://localhost:8080), that:
#   1. the page loads
#   2. the agent can reach the CRUD API
#   3. a demo user can log in, and the agent knows who it is
#   4. the CRUD API is connected to the database as gatekeeper_app, never as the owner
#
# It uses its own project name, so it never touches your normal Docker data, and it deletes
# everything it made at the end.

set -euo pipefail   # stop at the first command that fails

COMPOSE="docker compose -p gatekeeper-smoke-test -f compose.yaml -f .github/compose.ci.yaml"
PAGE=http://localhost:8080
COOKIES=$(mktemp)

# Whatever happens (pass or fail): show the logs if it failed, then remove everything.
finish() {
    status=$?
    if [ $status -ne 0 ]; then
        echo "FAILED. The last log lines of each container:"
        $COMPOSE logs --tail 40 || true
    fi
    $COMPOSE down -v > /dev/null 2>&1 || true
    rm -f "$COOKIES"
    exit $status
}
trap finish EXIT

echo "Building and starting (no AI model)..."
$COMPOSE up -d --build agent frontend

echo "Waiting for the agent to reach the CRUD API..."
for i in $(seq 1 60); do
    if curl -s "$PAGE/api/health" | grep -q '"crud_api":"ok"'; then break; fi
    if [ "$i" -eq 60 ]; then echo "The agent never reached the CRUD API."; exit 1; fi
    sleep 2
done
echo "PASS  the agent reaches the CRUD API"

curl -sf "$PAGE/" | grep -q '<div id="root">'
echo "PASS  the page loads"

curl -sf -c "$COOKIES" -H "Content-Type: application/json" \
    -d '{"email":"sara.wilson@gatekeeper.local","password":"Password123!"}' "$PAGE/api/auth/login" > /dev/null
curl -sf -b "$COOKIES" "$PAGE/api/me" | grep -q "sara.wilson@gatekeeper.local"
echo "PASS  Sara can log in"

# Every connection to the app's database except this check's own one (pg_backend_pid).
users=$($COMPOSE exec -T db psql -U postgres -d gatekeeper_hr -tAc \
    "SELECT DISTINCT usename FROM pg_stat_activity WHERE datname = 'gatekeeper_hr' AND pid <> pg_backend_pid()")
if [ "$users" != "gatekeeper_app" ]; then
    echo "The CRUD API should connect only as gatekeeper_app, but these users are connected: $users"
    exit 1
fi
echo "PASS  the CRUD API connects as gatekeeper_app"

echo "All Docker checks passed."
