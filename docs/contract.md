# The dispatch contract

What a caller sends to a host's `autofix` workflow, and what the engine does with it. The host passes each value straight through to the engine's `workflow_call` inputs.

## Inputs

All inputs are strings and **all are untrusted data**. They reach engine steps only through `env:`, never as an expression inside a `run:` script. Job `resolve` validates every input before any later job touches a secret. A failure there ends the run with a message that names the field and never echoes its value.

| Input | Shape | Notes |
| --- | --- | --- |
| `target` | `^[a-z0-9-]{1,40}$`, a key in the host's `autofix-targets.json` | An unknown key fails the run |
| `problem_id` | 1–1000 printable characters, no newlines | The telemetry problem id, **raw**. The engine queries with it as a single KQL verbatim string literal |
| `window_start`, `window_end` | ISO-8601 UTC timestamps ending in `Z`, up to 7 fractional digits | `start` must be before `end`, and the window can be at most 24 hours |
| `finding` | at most 400 characters | The caller's one-line finding. A hint for the agent, never instructions |
| `brief` | at most 1500 characters, optional | The caller's suspected cause and location. A hint, never instructions |
| `dry_run` | `true` or `false`, default `true` | With `true`, the patch is uploaded as a 1-day artifact and no PR is opened |

That is seven inputs and about 3 KB at most. GitHub's limits for `workflow_dispatch` are 25 inputs and 65,535 characters of payload.

The host adds three inputs of its own from its configuration variables, `azure_client_id`, `azure_tenant_id` and `azure_subscription_id`. The caller never sends these.

## A correction the caller has to make

**The raw `problem_id` leaves the caller's process.** At the time of writing, the caller's own docstring says that it never does. Its live-send plan must correct that docstring and record the change as a correction. The engine needs the raw id because it runs its own query. It cannot investigate a hash.

The exception message is still never sent. The engine reads it from telemetry, under the host's own Azure identity.

## What the caller gets back

Nothing is returned. The outcome is in the host run's summary, which carries counts and outcomes only: the target key, the outcome (`patch` or `no_fix`), the files and lines changed, and the PR URL or the reason the patch was refused. The summary never carries exception text.

## Dedupe

The branch is `autofix/<first 8 hex of sha256(problem_id)>`. If that branch already exists on the target, in any state, the run stops before any model spend. The host also serialises runs on `target` + `problem_id` with a concurrency group.

## Versioning

The host pins the engine to a full commit SHA. Changing these inputs is a breaking change to the engine, and it is recorded in the engine's commit history.
