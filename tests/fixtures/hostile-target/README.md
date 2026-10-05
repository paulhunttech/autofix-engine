# Hostile target fixture

Used only by the CI canary (`tests/canary.sh`). It stands in for a target repository that ships its own Claude Code configuration:

- `.claude/settings.json` has a `SessionStart` hook that writes `hook.marker`, broad allow rules, and `enableAllProjectMcpServers: true`;
- `.mcp.json` has a stdio server that writes `mcp.marker` when it starts.

Run through `scripts/run_agent.sh`, neither marker may appear. Run with the isolation flags dropped (the positive control), both must appear, or the canary proves nothing.
