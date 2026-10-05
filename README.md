# autofix-engine

Turns an investigated production exception into a **draft** pull request. A human reviews it, and only a human merges it.

The engine is generic. It names no organisation, repository, tenant or workspace. Everything specific to one estate lives in that estate's own **host** repository.

## The three layers

| Layer | Where | Holds |
| --- | --- | --- |
| **Caller** | whatever watches the alerts (for its author, a personal chief-of-staff system) | Investigates an alert, decides it is code-fixable, and dispatches the host's workflow with its analysis |
| **Engine** | this repository, public | One reusable workflow, `.github/workflows/engine.yml`, in five jobs that each hold the least they need (below) |
| **Host** | a small private repository in the target's organisation | A caller workflow that `uses:` this engine **pinned to a full commit SHA**, a targets config, the secrets, and the dispatch App, installed on the host only |

Adding a repository in an organisation that already has a host means adding one entry to the host's targets config. Adding a new organisation means creating one small host repository.

*This corrects the engine row as first written, which described two jobs. The engine now has five. The agent's job holds no GitHub or Azure credential at all, and telemetry is read by a separate job with no model in it.*

## The engine's jobs

| Job | Holds | Does |
| --- | --- | --- |
| `resolve` | no secrets | Validates every input and resolves the target from the host's config |
| `fetch` | the PR App key, downscoped to read one repo | Stops if the `autofix/…` branch already exists, then tars the target's default branch, `.git` included |
| `evidence` | an Azure OIDC login only | Runs fixed, engine-owned KQL and writes `evidence.json`. The model never runs a query |
| `agent` | the Anthropic key only | Runs Claude Code with file tools only (no shell, no web, no MCP), with the target's own Claude configuration ignored, and produces a patch |
| `pr` | the PR App key, write on one repo | Refuses any patch outside the allowlist, binary or oversized, or carrying external references in rendered files, then pushes the branch and opens a **draft** PR with the model's text fenced |

- [`docs/contract.md`](docs/contract.md): what a caller sends.
- [`docs/host-setup.md`](docs/host-setup.md): the operator runbook for a host and its targets, including the two onboarding gates that come before any live run.

## Invariants

These hold for every change to this repository. Flag any change that would break one.

1. **Generic.** Nothing here names an estate. If a value is true of one target, it belongs in the host's config or in the target repository's own `CLAUDE.md`.
2. **Draft PRs only, and never a merge.** The engine can open a draft PR and nothing else, and it cannot merge.
3. **The agent's job holds no write credential.** It may read the target and the evidence gathered for it. The PR App key is held by two jobs, `fetch` and `pr`, and only `pr` mints a token that can write. `fetch` mints a token downscoped to read one repository, and runs no model and no target code. `pr` writes only paths on the allowlist. *This corrects "Only the PR job holds a key that can write", which overlooked that `fetch` holds the same key and is restrained only by the token it chooses to mint.*
4. **Inputs are data.** Every dispatch input reaches a step through `env:`, never interpolated as `${{ inputs.* }}` into a script.
5. **The host pins a SHA.** Secrets cross the organisation boundary by name, so the job-level separation in invariant 3 is only as trustworthy as the commit the host pinned.
