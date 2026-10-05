#!/usr/bin/env bash
# The isolation canary. Runs the real scripts/run_agent.sh against the hostile fixture
# with an invalid API key and a dead API endpoint, so the session fails right after
# start-up and nothing is ever sent to a model.
#
# Usage: tests/canary.sh restricted     neither fixture marker may appear
#        tests/canary.sh unrestricted   positive control: BOTH fixture markers must appear
#
# Each marker is checked separately, so one mechanism cannot hide behind the other.
#
# Both runs also pass a SessionStart hook through --settings, which --restricted still
# loads, and require its marker. That proves the session really started: without it, a CLI
# bump that rejects a flag at parse time would write no marker and pass the restricted half
# vacuously.
set -euo pipefail

mode=${1:-}
case "$mode" in
  restricted|unrestricted) ;;
  *) echo "usage: canary.sh restricted|unrestricted" >&2; exit 2 ;;
esac

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/work" "$tmp/home"
cp -R "$repo/tests/fixtures/hostile-target/." "$tmp/work/"

export HOME="$tmp/home"
export ANTHROPIC_API_KEY="sk-ant-canary-not-a-key"
export ANTHROPIC_BASE_URL="http://127.0.0.1:9"
export CLAUDE_CODE_MAX_RETRIES=0
if [[ "$mode" == "unrestricted" ]]; then
  export AUTOFIX_CANARY_UNRESTRICTED=1
fi
started="$tmp/session-started.marker"
printf '{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"touch %s"}]}]}}\n' "$started" >"$tmp/liveness.json"
export AUTOFIX_CANARY_SETTINGS="$tmp/liveness.json"

timeout 180 "$repo/scripts/run_agent.sh" "$tmp/work" "$tmp/out" || echo "run_agent.sh exited $?"
sleep 2  # let a just-spawned hook or server finish its touch

fail=0
if [[ -e "$started" ]]; then
  echo "ok: $mode, the session started (the --settings hook ran)"
else
  echo "FAIL: the --settings SessionStart hook did not run, so the session never started and this run proves nothing"
  head -c 2000 "$tmp/out/claude.err" || true
  fail=1
fi
for marker in hook.marker mcp.marker; do
  if [[ -e "$tmp/work/$marker" ]]; then present=yes; else present=no; fi
  if [[ "$mode" == "restricted" && "$present" == "yes" ]]; then
    echo "FAIL: $marker was written with the isolation flags on"
    fail=1
  elif [[ "$mode" == "unrestricted" && "$present" == "no" ]]; then
    echo "FAIL: $marker was NOT written with the isolation flags off, so this half of the canary proves nothing"
    fail=1
  else
    echo "ok: $mode, $marker present=$present"
  fi
done
exit "$fail"
