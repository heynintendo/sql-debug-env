#!/usr/bin/env bash
# validate-submission.sh — OpenEnv Submission Validator
# Checks: HF Space is live, Docker image builds, openenv validate passes.
# Usage: ./validate-submission.sh <ping_url> [repo_dir]

set -uo pipefail
DOCKER_BUILD_TIMEOUT=600
if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED='' GREEN='' YELLOW='' BOLD='' NC=''
fi

run_with_timeout() {
  local secs="$1"; shift
  if command -v timeout &>/dev/null; then timeout "$secs" "$@"
  elif command -v gtimeout &>/dev/null; then gtimeout "$secs" "$@"
  else "$@" & local pid=$!; ( sleep "$secs" && kill "$pid" 2>/dev/null ) & local watcher=$!; wait "$pid" 2>/dev/null; local rc=$?; kill "$watcher" 2>/dev/null; wait "$watcher" 2>/dev/null; return $rc; fi
}

PING_URL="${1:-}"; REPO_DIR="${2:-.}"
[ -z "$PING_URL" ] && { printf "Usage: %s <ping_url> [repo_dir]\n" "$0"; exit 1; }
REPO_DIR="$(cd "$REPO_DIR" 2>/dev/null && pwd)" || { printf "Error: directory not found\n"; exit 1; }
PING_URL="${PING_URL%/}"; PASS=0

log()  { printf "[%s] %b\n" "$(date -u +%H:%M:%S)" "$*"; }
pass() { log "${GREEN}PASSED${NC} -- $1"; PASS=$((PASS + 1)); }
fail() { log "${RED}FAILED${NC} -- $1"; }
hint() { printf "  ${YELLOW}Hint:${NC} %b\n" "$1"; }
stop_at() { printf "\n${RED}${BOLD}Validation stopped at %s.${NC} Fix above.\n" "$1"; exit 1; }

printf "\n${BOLD}========================================${NC}\n"
printf "${BOLD}  OpenEnv Submission Validator${NC}\n"
printf "${BOLD}========================================${NC}\n"
log "Repo: $REPO_DIR"; log "URL:  $PING_URL"; printf "\n"

# Step 1: Ping HF Space
log "${BOLD}Step 1/3: Pinging HF Space${NC} ($PING_URL/reset) ..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" -d '{}' "$PING_URL/reset" --max-time 30 2>/dev/null || printf "000")
[ "$HTTP_CODE" = "200" ] && pass "HF Space live" || { fail "HF Space /reset returned $HTTP_CODE"; stop_at "Step 1"; }

# Step 2: Docker build
log "${BOLD}Step 2/3: Docker build${NC} ..."
command -v docker &>/dev/null || { fail "docker not found"; stop_at "Step 2"; }
if [ -f "$REPO_DIR/Dockerfile" ]; then DC="$REPO_DIR"; elif [ -f "$REPO_DIR/server/Dockerfile" ]; then DC="$REPO_DIR/server"; else fail "No Dockerfile"; stop_at "Step 2"; fi
run_with_timeout "$DOCKER_BUILD_TIMEOUT" docker build "$DC" >/dev/null 2>&1 && pass "Docker build OK" || { fail "Docker build failed"; stop_at "Step 2"; }

# Step 3: openenv validate
log "${BOLD}Step 3/3: openenv validate${NC} ..."
command -v openenv &>/dev/null || { fail "openenv not found"; stop_at "Step 3"; }
(cd "$REPO_DIR" && openenv validate 2>&1) && pass "openenv validate OK" || { fail "openenv validate failed"; stop_at "Step 3"; }

printf "\n${GREEN}${BOLD}  All 3/3 checks passed!${NC}\n\n"
exit 0
