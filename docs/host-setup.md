# Setting up a host

This is the operator runbook. It creates a **host**, the small private repository that calls the engine for one organisation, and it onboards a **target**, a repository the engine may open draft PRs against.

The commands are PowerShell, and use the GitHub CLI (`gh`) and the Azure CLI (`az`). Set these once per session:

```powershell
$Org = 'your-org'                      # the organisation that owns the host and its targets
$HostRepo = "$Org/autofix-host"        # the host repository
$EngineSha = '<full 40-char sha>'      # the engine commit you have reviewed
```

## 1. Create the host

1. Create a **private** repository `$HostRepo`.
2. Copy in the two files from `examples/host/` in this repository:
   - `.github/workflows/autofix.yml`. Replace `OWNER` with this engine's owner, and replace the all-zero SHA with `$EngineSha`. **Pin a full SHA, never a branch or a tag**, because the job separation the engine relies on is only as trustworthy as the commit you pinned.
   - `autofix-targets.json`. Leave `targets` empty until a target has passed both onboarding gates (section 4).

That is all the host holds: two files, three secrets and three variables.

## 2. The two GitHub Apps

### The PR App: installed on the targets only

Create a GitHub App owned by `$Org`. Give it:

- **Repository permissions:** Contents *Read and write*, Pull requests *Read and write*, and Metadata *Read* (GitHub requires it).
- **Nothing else.** In particular, it gets **no Workflows permission**, so it cannot push a change to `.github/workflows/`.
- **Webhook:** off.

Install it on **the target repositories only**, not on the host and not on all repositories. Generate a private key, then store the App's Client ID and the key in the host:

```powershell
gh secret set PR_APP_CLIENT_ID --repo $HostRepo --body '<client id>'
gh secret set PR_APP_PRIVATE_KEY --repo $HostRepo < .\pr-app.private-key.pem
```

Delete the local `.pem` once it is stored.

⚠️ **Until gate 1 passes on a target, this key can release to production.** Contents *write* can fast-forward any unprotected branch, including a `main` that deploys on push, and can create a tag that a release workflow deploys.

### The dispatch App: installed on the host only

The caller uses this App to start the host's workflow. Create it with **Actions *Read and write*** and nothing else, and install it on **the host only**. The caller holds its key, and the engine never sees it.

## 3. Secrets and variables

```powershell
gh secret set ANTHROPIC_API_KEY --repo $HostRepo
gh variable set AZURE_CLIENT_ID --repo $HostRepo --body '<client id of the identity below>'
gh variable set AZURE_TENANT_ID --repo $HostRepo --body '<tenant id>'
gh variable set AZURE_SUBSCRIPTION_ID --repo $HostRepo --body '<subscription id>'
```

### The Azure identity

The `evidence` job logs in with OpenID Connect, so the host holds **no Azure secret**.

1. Create an app registration, or a user-assigned managed identity, for the host.
2. Add a **federated credential**:
   - issuer `https://token.actions.githubusercontent.com`;
   - audience `api://AzureADTokenExchange`;
   - subject `<prefix>:ref:refs/heads/main`, where `<prefix>` is what GitHub reports for the host repo (below).

   In a reusable workflow the token's subject names the **caller**, which is the host, so the credential is federated to the host and not to the engine.

   ⚠️ **Read the prefix from GitHub; don't write it as `repo:<org>/<repo>`.** A repo using GitHub's immutable subject format issues `repo:<org>@<org id>/<repo>@<repo id>`, which never matches the plain form. The login then fails with `AADSTS700213: No matching federated identity record`. Ask the repo directly: `gh api "repos/$HostRepo/actions/oidc/customization/sub"` returns `sub_claim_prefix`, the exact prefix its tokens carry. *This corrects the subject as first written, `repo:<org>/<host repo>:ref:refs/heads/main`, which failed on the first host (2026-10-06).*
3. Grant it **Log Analytics Reader** on each workspace a target lists, and nothing wider.

```powershell
$Prefix = gh api "repos/$HostRepo/actions/oidc/customization/sub" --jq '.sub_claim_prefix'
@{ name = 'autofix-host-main'; issuer = 'https://token.actions.githubusercontent.com'; subject = "$($Prefix):ref:refs/heads/main"; audiences = @('api://AzureADTokenExchange') } | ConvertTo-Json | Set-Content fic.json
az ad app federated-credential create --id '<app object id>' --parameters fic.json
az role assignment create --assignee '<client id>' --role 'Log Analytics Reader' --scope '<workspace resource id>'
```

## 4. Onboarding a target: both gates come first

Do not add a target to `autofix-targets.json` with `dry_run: false` in mind until **both** gates are done. A `dry_run: true` run is safe before then, because it never mints a write token.

```powershell
$Target = "$Org/<target repo>"
```

### Gate 1: branch and tag rulesets that the PR App cannot bypass

Contents *write* lets the PR App create, move and delete **tags** as well as branches. So a target that deploys on a tag push needs its tags protected too, not only its branches. Create **two** rulesets on the target:

- one covering **every branch except `autofix/**` and the integration branch**, meaning the branch the engine's PRs merge into (the default branch, `$Integration` below);
- one covering **every tag**.

Each restricts creation, update and deletion. Their bypass actors are the people and roles who push today. **The PR App must not be one of them.**

⚠️ **The integration branch must be excluded, and it must already require a pull request through its own ruleset or branch protection.**
- **Why exclude it:** merging a PR *updates* that branch, and GitHub applies a bypass to a merge only when the person merging asks for it explicitly. That means ticking "bypass rules", or `gh pr merge --admin`.
- **What happens if it isn't excluded:** every ordinary merge into it fails with "Cannot update this protected ref". This was observed on the first target, on 2026-10-06.
- **Why it's still safe:** its own PR requirement already stops the PR App pushing to it directly. Gate 2 keeps the required check red until a person has read an autofix PR, so the App can't merge one either.

*This corrects the gate as first written, which covered every branch, the integration branch included.* If the integration branch doesn't require a PR, add that first: it is the protection this exclusion relies on.

```powershell
$Integration = gh api "repos/$Target" --jq '.default_branch'   # the branch PRs merge into, if not the default
$Bypass = @(
  # The roles that push today. For example, the repository admin role (role id 5):
  @{ actor_id = 5; actor_type = 'RepositoryRole'; bypass_mode = 'always' }
)
$Rules = @(@{ type = 'creation' }, @{ type = 'update' }, @{ type = 'deletion' })
$Branches = @{
  name = 'autofix: protect every non-autofix branch'; target = 'branch'; enforcement = 'active'
  conditions = @{ ref_name = @{ include = @('~ALL'); exclude = @('refs/heads/autofix/**', "refs/heads/$Integration") } }
  rules = $Rules; bypass_actors = $Bypass
} | ConvertTo-Json -Depth 6
$Tags = @{
  name = 'autofix: protect every tag'; target = 'tag'; enforcement = 'active'
  conditions = @{ ref_name = @{ include = @('~ALL'); exclude = @() } }
  rules = $Rules; bypass_actors = $Bypass
} | ConvertTo-Json -Depth 6
$Branches | gh api --method POST "repos/$Target/rulesets" --input -
$Tags | gh api --method POST "repos/$Target/rulesets" --input -
```

Adjust `$Bypass` before you run it. Anyone who creates or pushes branches other than the integration branch, or pushes tags, needs a bypass entry, or that push stops working. That includes release pushes to a production branch. Check who pushes today with `gh api --paginate "repos/$Target/activity?per_page=100&time_period=quarter" --jq '.[].actor.login'`.

**Read it back.** This check must print `PASS`. It probes:
- the integration branch, which must keep its PR requirement and must **not** be update-restricted, or merges break;
- each branch you name in `$Deploying`, typically the branches whose push deploys, which must be update-restricted;
- a made-up branch name, which must be protected;
- an `autofix/` name, which must not be.

The branch-rules endpoint answers for names that don't exist. The check also confirms an active tag ruleset, and that no ruleset, including organisation-level ones, lists the PR App as a bypass actor.

```powershell
$AppId = '<the PR App numeric app id>'
$Deploying = @('main')   # every branch whose push deploys, other than the integration branch
function Get-RuleTypes($Branch) { (gh api "repos/$Target/rules/branches/$Branch" | ConvertFrom-Json).type }
$IntegrationRules = Get-RuleTypes $Integration
$IntegrationOk = ($IntegrationRules -contains 'pull_request') -and -not ($IntegrationRules -contains 'update')
$DeployingOk = -not ($Deploying | Where-Object { -not ((Get-RuleTypes $_) -contains 'update') })
$OtherOk = (Get-RuleTypes 'autofix-gate-probe') -contains 'creation'
$AutofixFree = -not ((Get-RuleTypes 'autofix/gate-probe') | Where-Object { $_ -in 'creation', 'update', 'deletion' })
$Sets = foreach ($Id in (gh api "repos/$Target/rulesets?includes_parents=true" --jq '.[].id')) { gh api "repos/$Target/rulesets/$Id" | ConvertFrom-Json }
$TagOk = [bool]($Sets | Where-Object { $_.target -eq 'tag' -and $_.enforcement -eq 'active' -and ($_.rules.type -contains 'creation') -and ($_.conditions.ref_name.include -contains '~ALL') })
$AppBypasses = [bool]($Sets.bypass_actors | Where-Object { $_.actor_type -eq 'Integration' -and "$($_.actor_id)" -eq $AppId })
# GitHub returns bypass_actors only to a caller with write access to that ruleset. An
# organisation ruleset read by a repository admin comes back without it, and silence is not a pass.
$Unseen = @($Sets | Where-Object { -not $_.PSObject.Properties['bypass_actors'] })
if ($Unseen.Count -gt 0) { "FAIL: cannot read the bypass list of $($Unseen.Count) ruleset(s): $($Unseen.name -join ', '). Have an organisation owner run this check." }
elseif ($IntegrationOk -and $DeployingOk -and $OtherOk -and $AutofixFree -and $TagOk -and -not $AppBypasses) { 'PASS' }
else { "FAIL: integration=$IntegrationOk deploying=$DeployingOk other-branch=$OtherOk autofix-free=$AutofixFree tags=$TagOk app-can-bypass=$AppBypasses" }
```

- **`autofix-free` must be true**, or the engine can't create its branch. The protection is meant to stop exactly at `autofix/**`.
- **`integration` must be true**, or ordinary merges are blocked. Also merge one ordinary PR after applying the gate, as a live check. The read-back sees every ruleset, but not push restrictions in classic branch protection.

### Gate 2: the target's CI must not run model-written code with network access

A push made with the PR App's token triggers the target's `push` and `pull_request` workflows, and any that run on `push: tags:` if it could push a tag (gate 1 stops that). **CI then executes model-written code, with network access, before any human looks at it.** Anything the agent read can leave that way: the evidence, the repository, and whatever secrets those workflows expose.

List the target's workflows and their triggers:

```powershell
$Files = gh api "repos/$Target/contents/.github/workflows" --jq '.[].path'
foreach ($F in $Files) { "== $F"; gh api "repos/$Target/contents/$F" -H 'Accept: application/vnd.github.raw' | Select-String -Pattern '^\s*(on:|push:|pull_request|pull_request_target|workflow_run|branches|branches-ignore|tags|tags-ignore)' }
```

For **each** workflow that runs on a push to a non-default branch, or on a `pull_request`, do one of two things.

- **Gate it behind a person's label (recommended).** CI still runs on an autofix PR, but only after someone has read the diff.
  - **On a `push` trigger,** add `branches-ignore: ['autofix/**']`.
  - **On a `pull_request` trigger,** branch filters match the **base** branch, not the head, so a filter can't do it. Add `labeled` to its `types`. Then make the **first step of a job that every other job `needs`** fail on an `autofix/` head, unless the event is the `labeled` event for your label, sent by a person:

    ```yaml
    on:
      pull_request:
        types: [opened, synchronize, reopened, labeled]
    # …and as step 0 of the root job:
          - name: Autofix gate
            env:
              HEAD_REF: ${{ github.head_ref }}
              ACTION: ${{ github.event.action }}
              LABEL: ${{ github.event.label.name }}
              SENDER_TYPE: ${{ github.event.sender.type }}
            run: |
              case "$HEAD_REF" in autofix/*) ;; *) exit 0 ;; esac
              if [ "$ACTION" = "labeled" ] && [ "$LABEL" = "autofix-ci" ] && [ "$SENDER_TYPE" = "User" ]; then exit 0; fi
              echo "::error::Model-written autofix PR: CI waits for a person to add the 'autofix-ci' label."
              exit 1
    ```

    Create the label with `gh label create autofix-ci --repo $Target`.

  ⚠️ **Make it a failing step, never a job-level `if:`.** A *skipped* job reports success, even as a required check (GitHub's Actions troubleshooting reference). So `if:` conditions would let an autofix PR show green, and merge, with no CI run at all. A failed root job means every dependent job never runs, and the required check goes **red** until a person labels the PR. *This corrects the gate as first written, which prescribed a job-level `if:` on every job.*

  ⚠️ **Check the label event's sender, not whether the label is present.** The PR App's Pull requests *write* permission lets it add labels itself (GitHub's App permissions reference lists the add-labels endpoint under Pull requests). To re-run CI after a push to the branch, remove the label and add it again.
- **Or record acceptance of this specific risk, in these words:** *"CI on this repository runs model-written code from autofix branches with network access before review. Anything in the agent's context, meaning the projected telemetry and this repository, can be sent anywhere by that code."* Record who accepted it and when, in the host's own tracker.

`pull_request_target` and `workflow_run` workflows run with the base repository's secrets. Treat any that react to autofix branches as failing this gate until they are skipped.

### Add the target

When both gates pass, add the entry to the host's `autofix-targets.json`:

```json
{ "targets": { "<key>": { "repo": "<org>/<target repo>", "allow": ["src/**", "tests/**"], "workspace": "<log analytics workspace id>" } } }
```

Keep `allow` as narrow as the code the agent should touch. The engine refuses `.github/` and `.autofix/` whatever the allowlist says.

## 5. First runs

1. **Dry run.** Nothing is pushed:

   ```powershell
   gh workflow run autofix.yml --repo $HostRepo -f target='<key>' -f problem_id='<problem id>' -f window_start='<start>Z' -f window_end='<end>Z' -f finding='<one line>' -f brief='<cause and location>' -f dry_run=true
   ```

   **Pass:** the run ends with either an `agent-result` artifact holding a patch, or `no_fix` with a reason. The run summary and every job log must contain **no exception text**. Read them to check.
2. **One live run** with `-f dry_run=false`. **Pass:** a draft PR appears on an `autofix/…` branch, its body is fenced, and the target's CI did not run on it (or gate 2 recorded acceptance).

## Updating the engine

Read the engine diff between the SHA you pinned and the new one, then change the SHA in `autofix.yml`. Never point the host at a branch.
