#!/usr/bin/env bash

set -euo pipefail

AGENT_DIR="${1:-spogo_song_agent}"

if [[ ! -d "$AGENT_DIR" ]]; then
  echo "FAIL: Agent directory '$AGENT_DIR' does not exist."
  exit 1
fi

fail_count=0

pass() {
  echo "PASS: $1"
}

fail() {
  echo "FAIL: $1"
  fail_count=$((fail_count + 1))
}

check_file() {
  local relative_path="$1"
  if [[ -f "$AGENT_DIR/$relative_path" ]]; then
    pass "$AGENT_DIR/$relative_path exists."
  else
    fail "$AGENT_DIR/$relative_path is missing."
  fi
}

check_file "agent.py"
check_file "__init__.py"
check_file "requirements.txt"

if [[ -f "$AGENT_DIR/__init__.py" ]]; then
  if grep -Eq '^[[:space:]]*from[[:space:]]+\.[[:space:]]+import[[:space:]]+agent[[:space:]]*$' "$AGENT_DIR/__init__.py"; then
    pass "$AGENT_DIR/__init__.py exports the agent module."
  else
    fail "$AGENT_DIR/__init__.py must contain: from . import agent"
  fi
fi

if [[ -f "$AGENT_DIR/agent.py" ]]; then
  if grep -Eq '^[[:space:]]*root_agent[[:space:]]*=' "$AGENT_DIR/agent.py"; then
    pass "$AGENT_DIR/agent.py defines root_agent."
  else
    fail "$AGENT_DIR/agent.py must define root_agent = Agent(...)."
  fi
fi

if [[ "$fail_count" -gt 0 ]]; then
  echo ""
  echo "Validation failed with $fail_count issue(s)."
  exit 1
fi

echo ""
echo "Cloud Run Step 1 validation passed for '$AGENT_DIR'."
