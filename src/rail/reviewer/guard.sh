#!/usr/bin/env bash
# red-rail reviewer guard for agy (headless-agents PreToolUse hook): a judge reads the PR as
# data and never acts. Only an MCP call is allowed — and the review profile configures no
# MCP server — everything else (run_command, write_to_file, …) is denied.
set -euo pipefail
payload="$(cat)"
name="$(printf '%s' "$payload" | python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("toolCall", {}).get("name", ""))
except Exception:
    print("")' 2>/dev/null || true)"
if [ "$name" = "call_mcp_tool" ]; then
  printf '{"decision":"allow"}'
else
  printf '{"decision":"deny","reason":"red-rail reviewer: judges read, never act"}'
fi
