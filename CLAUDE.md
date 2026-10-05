# Notes for Claude Code

## What this is

A generic, public engine that turns an investigated production exception into a draft PR. It is called as a reusable workflow from a thin per-organisation host repository. `README.md` has the invariants. They are load-bearing, so flag it if a change would break one.

## Constraints

1. **This repository is public.** Never commit secrets, tokens, App ids, private keys, webhook URLs, or anything an attacker could use.
2. **Name no estate.** That means no organisation, employer, repository, tenant, subscription, workspace or resource name, in the files, the commit messages or the issues. The issues are public too. Anything specific to one estate belongs in its host repository or its runtime configuration.
3. **No operational payloads.** Describe *how* the engine works, never *what* it saw: no exception text, log extracts or personal data.
4. **Check platform facts against the vendor's own docs** (GitHub, Azure, Anthropic) before writing them down.
5. **Say when you're correcting something.** Write *"this corrects X"* rather than quietly editing it.
6. **Keep the host small.** If a feature needs the host to carry more than config, secrets and one pinned `uses:` line, that is a design smell. Say so.

Shell commands written **for Paul to run** are PowerShell on Windows: backtick continuation, not backslash, and `$Var = value`. Commands you run yourself follow your own tooling.
