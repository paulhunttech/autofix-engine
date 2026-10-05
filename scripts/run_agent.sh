#!/usr/bin/env bash
# The agent invocation, in one place. engine.yml and the CI canary both call this
# script, so the canary tests the real flags rather than a copy.
#
# Usage: run_agent.sh <work dir> <out dir>
#   <work dir>  the target's tree, with evidence and hints under .autofix/
#   <out dir>   where result.raw, claude.err and claude.rc are written (outside <work dir>)
#
# The agent gets file tools only: no Bash, no web tool, no MCP. --restricted ignores
# the target's own .claude settings, hooks and plugins and confines the file tools to
# the working directory; --strict-mcp-config with no --mcp-config skips its .mcp.json.
# Claude's stdout and stderr go to files, never to the job log.
#
# Two test-only switches, for the canary only. tests/test_workflow.py asserts that
# engine.yml never sets either:
#   AUTOFIX_CANARY_UNRESTRICTED=1     drops --restricted and --strict-mcp-config (the positive control)
#   AUTOFIX_CANARY_SETTINGS=<file>    adds --settings <file>, which --restricted still loads, so the
#                                     restricted run can prove the session actually started
set -euo pipefail

MAX_TURNS=60
MAX_BUDGET_USD=5
TASK="Investigate the production exception described in .autofix/ and propose the smallest fix, following your instructions."

if [[ $# -ne 2 ]]; then
  echo "usage: run_agent.sh <work dir> <out dir>" >&2
  exit 2
fi
work=$(cd "$1" && pwd)
out=$(mkdir -p "$2" && cd "$2" && pwd)
engine=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

isolation=(--restricted --strict-mcp-config)
if [[ "${AUTOFIX_CANARY_UNRESTRICTED:-}" == "1" ]]; then
  isolation=()
fi
if [[ -n "${AUTOFIX_CANARY_SETTINGS:-}" ]]; then
  isolation+=(--settings "$AUTOFIX_CANARY_SETTINGS")
fi

cd "$work"
rc=0
CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1 claude -p "${isolation[@]}" \
  --permission-mode dontAsk --permission-prompts none \
  --tools "Read,Edit,Write,Glob,Grep" \
  --allowedTools "Read" "Glob" "Grep" "Edit(./**)" "Write(./**)" \
  --disallowedTools "mcp__*" \
  --max-turns "$MAX_TURNS" --max-budget-usd "$MAX_BUDGET_USD" \
  --append-system-prompt-file "$engine/prompts/agent.md" \
  --json-schema "$(cat "$engine/schemas/result.json")" \
  --output-format json "$TASK" \
  </dev/null >"$out/result.raw" 2>"$out/claude.err" || rc=$?
echo "$rc" >"$out/claude.rc"
echo "claude exited $rc"
