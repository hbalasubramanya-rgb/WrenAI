# WrenAI Handoff for Sudhanva

Updated: 2026-09-15

This is the day-to-day workflow for Sudhanva (`INT\v_sjanardhan`).

Sudhanva will use the WrenAI repository that already exists on his local
machine. He does not need to clone WrenAI again. The shared dev server also
already has its runtime versions, virtual environments, configuration, secrets,
data, and Windows Scheduled Tasks. Do not reinstall or replace them for normal
source-code changes.

## Workflow Summary

1. Update the existing repository on Sudhanva's local machine.
2. Make and test source-code changes locally.
3. Commit and push the selected files from the local machine.
4. Sign in to the dev server and use the existing `D:\WrenAI` checkout.
5. Fetch and fast-forward the dev checkout.
6. Install dependencies only when a dependency manifest or lock file changed.
7. Restart the affected service and verify its health.

Shared branch:

```text
organization/ask-schema-grounding-20260820
```

## A. Work on Sudhanva's Local Machine

### A1. Update the existing local checkout

Open PowerShell in the WrenAI repository that is already on the local machine:

```powershell
Set-Location <existing-local-WrenAI-path>

$Branch = "organization/ask-schema-grounding-20260820"

git status --short --branch
git switch $Branch
git pull --ff-only origin $Branch
```

If there are unfinished local changes, do not discard them. Finish, commit, or
safely preserve them before pulling.

### A2. Make and test the change

Edit only on the local machine. Run the tests related to the changed service.
Before committing, review the exact changes:

```powershell
git status --short
git diff
```

Never commit environment files, credentials, logs, virtual environments,
`node_modules`, `.next`, `.codex-tmp`, databases, Qdrant storage, downloaded
binaries, or other runtime files.

### A3. Commit only the intended files

```powershell
git add <file-1> <file-2>
git diff --cached
git commit -m "Describe the change"
git push origin $Branch
```

Do not use `git add .` when unrelated or generated files are present. Never
force-push this shared branch.

If the push is rejected because somebody pushed first:

```powershell
git fetch origin $Branch
git rebase "origin/$Branch"
```

Resolve and test any conflicts locally, then push normally:

```powershell
git push origin $Branch
```

## B. Deploy the Pushed Commit on the Dev Server

The dev-server checkout already exists at `D:\WrenAI`. Do not clone another
copy and do not recreate its configuration.

Use PowerShell commands on the dev server instead of GitHub Desktop. GitHub
Desktop displays more than 100,000 local runtime files in this checkout, and
those files must not be committed.

### B1. Inspect the incoming deployment

```powershell
Set-Location D:\WrenAI

$Branch = "organization/ask-schema-grounding-20260820"

git fetch --recurse-submodules=no origin $Branch
git status --short --branch --untracked-files=no
git diff --name-only HEAD "origin/$Branch"
git merge-base --is-ancestor HEAD "origin/$Branch"

if ($LASTEXITCODE -ne 0) {
    throw "Dev HEAD cannot be fast-forwarded to origin/$Branch. Stop the deployment."
}

$PreviousCommit = git rev-parse HEAD
$PreviousCommit
```

Record `$PreviousCommit` before continuing. It is the rollback commit.

A tracked `M wren-engine` entry is known dev-server state. Stop and investigate
if any other unexpected tracked change appears. Untracked runtime files are
expected; never run `git clean`.

Do not run `git submodule update`, `git submodule sync`, or a recursive pull on
this server. Its engine checkout intentionally uses a special doubled path.

### B2. Identify the affected service

Use the changed-file list from the previous command:

| Changed path | Scheduled Task to restart |
|---|---|
| `wren-ai-service/...` | `WrenAI 04 AI Service` |
| `wren-ui/...` | `WrenAI 05 UI` |
| Documentation only | No service restart |

Engine or Ibis changes require separate review because their dev checkout is
non-standard. Do not update the root submodule automatically.

### B3. Fast-forward the dev checkout

Disable the watchdog while intentionally stopping services. Stop only the
affected service or services:

For a documentation-only deployment, skip the stop, restart, and watchdog
commands and run only the `git merge --ff-only` command below.

```powershell
Disable-ScheduledTask -TaskName "WrenAI Watchdog"

# Run when wren-ai-service files changed:
Stop-ScheduledTask -TaskName "WrenAI 04 AI Service"

# Run when wren-ui files changed:
Stop-ScheduledTask -TaskName "WrenAI 05 UI"

Set-Location D:\WrenAI
git merge --ff-only "origin/$Branch"
```

Do not commit, merge branches, resolve source conflicts, or push from the dev
server. If the fast-forward fails, restart any service that was stopped,
re-enable the watchdog, and resolve the Git problem on the local machine.

### B4. Install or build only when required

Ordinary AI-service Python source changes require only an AI-service restart.
Ordinary UI source changes require only a UI restart because dev currently runs
`next dev`.

If `wren-ai-service/pyproject.toml` or `wren-ai-service/poetry.lock` changed:

```powershell
Set-Location D:\WrenAI\wren-ai-service
& .\venv\Scripts\Activate.ps1
python -m poetry install --only main
deactivate
```

If `wren-ui/package.json` or `wren-ui/yarn.lock` changed:

```powershell
Set-Location D:\WrenAI\wren-ui
node .\.yarn\releases\yarn-4.5.3.cjs install --immutable
```

If UI database migration files changed, confirm that a database backup exists
and then run:

```powershell
Set-Location D:\WrenAI\wren-ui
node .\.yarn\releases\yarn-4.5.3.cjs migrate
```

Do not run a migration merely because UI source code changed.

### B5. Restart the affected service

```powershell
# Run when wren-ai-service files changed:
Start-ScheduledTask -TaskName "WrenAI 04 AI Service"

# Run when wren-ui files changed:
Start-ScheduledTask -TaskName "WrenAI 05 UI"

Enable-ScheduledTask -TaskName "WrenAI Watchdog"
```

For a documentation-only deployment, no application service restart is needed.
Ensure the watchdog remains enabled.

### B6. Verify the deployment

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:6333/healthz
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/v2/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5555/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:3000/
```

All endpoints should return HTTP 200.

Browser verification:

```text
http://ZUSEVRAIDEV01:3000/
```

Finally confirm that the dev checkout reached the pushed commit:

```powershell
Set-Location D:\WrenAI
$RemoteBranch = "origin/$Branch"
git rev-list --left-right --count "HEAD...$RemoteBranch"
git log -1 --oneline
```

The divergence result should be `0 0`.

## C. Roll Back a Failed Dev Deployment

Use the `$PreviousCommit` value recorded before deployment:

```powershell
Disable-ScheduledTask -TaskName "WrenAI Watchdog"
Stop-ScheduledTask -TaskName "WrenAI 05 UI"
Stop-ScheduledTask -TaskName "WrenAI 04 AI Service"

Set-Location D:\WrenAI
git reset --hard $PreviousCommit

Start-ScheduledTask -TaskName "WrenAI 04 AI Service"
Start-ScheduledTask -TaskName "WrenAI 05 UI"
Enable-ScheduledTask -TaskName "WrenAI Watchdog"
```

Run all five health checks again. If dependency lock files changed between the
two commits, reinstall dependencies from the restored commit before restarting.

This reset is only for recovering the dev server. To undo the change on the
shared branch, create a `git revert` commit on the local machine, test it, push
it, and deploy that new commit normally.

## Important Rules

- Make source changes, commits, and pushes only from the local machine.
- On dev, only fetch, fast-forward, install/build when required, restart, and
  verify.
- Do not clone or recreate the dev environment.
- Do not copy or commit `.env` files or secrets.
- Do not commit the dev server's runtime files or `wren-engine` modification.
- Never use `git clean`, force-push, or root-level submodule-update commands.
