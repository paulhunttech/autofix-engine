# autofix-engine

Turns an investigated production exception into a **draft** pull request. A human reviews it, and only a human merges it.

The engine is generic. It names no organisation, repository, tenant or workspace. Everything specific to one estate lives in that estate's own **host** repository.

## The three layers

| Layer | Where | Holds |
| --- | --- | --- |
| **Caller** | whatever watches the alerts (for its author, a personal chief-of-staff system) | Investigates an alert, decides it is code-fixable, and dispatches the host's workflow with its analysis |
| **Engine** | this repository, public | One reusable workflow. **Job 1** runs the agent with no App key and produces a patch. **Job 2** checks the patch against the target's path allowlist and opens a draft PR with the PR App |
| **Host** | a small private repository in the target's organisation | A caller workflow that `uses:` this engine **pinned to a full commit SHA**, a targets config, the secrets, and the dispatch App, installed on the host only |

Adding a repository in an organisation that already has a host means adding one entry to the host's targets config. Adding a new organisation means creating one small host repository.

## Invariants

These hold for every change to this repository. Flag any change that would break one.

1. **Generic.** Nothing here names an estate. If a value is true of one target, it belongs in the host's config or in the target repository's own `CLAUDE.md`.
2. **Draft PRs only, and never a merge.** The engine can open a draft PR and nothing else, and it cannot merge.
3. **The agent's job holds no write credential.** It may read the target and its telemetry. Only the PR job holds a key that can write, and it writes only paths on the allowlist.
4. **Inputs are data.** Every dispatch input reaches a step through `env:`, never interpolated as `${{ inputs.* }}` into a script.
5. **The host pins a SHA.** Secrets cross the organisation boundary by name, so the job-level separation in invariant 3 is only as trustworthy as the commit the host pinned.
