---
name: ticket
description: This project's ticket adapter for /execute-task, backed by GitHub Issues. Exposes six verbs — fetch, record, status, comment, complete, cancel — and nothing else. Called by the pipeline, not usually by a person.
argument-hint: "fetch <ref> | record <draft> [--comment-only] | status <ref> not-started|started|in-review|blocked | comment <ref> <text-file> | complete <ref> [--user-requested] | cancel <ref> --user-requested"
---

**Values only.** The verbs, phases, sentinel, flags and refusals are the contract's, at
`~/.claude/skills/execute-task/SKILL.md`. The `gh` mechanics and their traps are the tracker
template's. This file adds only what is true of this repo.

⚠️ **This repo and its issues are public.** Before writing anything to an issue, check that it
names no organisation, repository, tenant or workspace (`CLAUDE.md`, constraint 2). If a
draft does, report it as an error rather than writing it.

## Settings

| Setting | Value |
| --- | --- |
| Tracker | GitHub |
| Repo | `paulhunttech/autofix-engine` — pass `--repo` explicitly on every call |
| Bare number | **is** a reference to an issue in that repo |
| Status mechanism | labels |
| Assignee, milestone, project board | none — set none of them |

## Phase → label

| phase | label |
| --- | --- |
| `not-started` | *(none — every status label removed)* |
| `started` | `in-progress` |
| `in-review` | `in-review` |
| `blocked` | `blocked` |

The whole status-label set is `in-progress`, `in-review`, `blocked`.

## Local notes

- **`Priority` and `Due date`**: GitHub Issues has neither field, and this repo has no
  Projects board. So say in the return that they were not recorded, and give their values.
- **`complete`**: this repo has no release step, so the only legitimate call is
  `complete <ref> --user-requested`. Refuse a bare `complete`.
- **Leaf test**: `.subIssues.totalCount == 0`. For `cancel`, refuse only if a node in
  `.subIssues.nodes` has `state == "OPEN"`.
