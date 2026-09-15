# Complete WrenAI Handoff for Sudhanva

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

## 1. Responsibility and Environment

Sudhanva's responsibility is:

- make and test code changes in the existing local checkout
- commit and push only intended source files
- deploy the pushed commit using the existing `D:\WrenAI` dev checkout
- build or install only when the incoming files require it
- restart only the affected services
- verify every deployment and roll back if verification fails

The dev server is a shared runtime, not a development worktree. Its existing
configuration and data belong to the server and must be preserved.

### Dev-server paths

| Component | Existing path |
|---|---|
| WrenAI root | `D:\WrenAI` |
| AI Service | `D:\WrenAI\wren-ai-service` |
| UI | `D:\WrenAI\wren-ui` |
| Outer engine checkout | `D:\WrenAI\wren-engine` |
| Actual engine | `D:\WrenAI\wren-engine\wren-engine` |
| Ibis Server | `D:\WrenAI\wren-engine\wren-engine\ibis-server` |
| Java legacy engine | `D:\WrenAI\wren-engine\wren-engine\wren-core-legacy` |
| Qdrant | `D:\WrenAI\qdrant` |
| Startup scripts | `D:\WrenAI\scripts` |
| Logs | `D:\WrenAI\logs` |

The doubled engine path is intentional server state. Do not normalize it during
a routine deployment.

### Runtime versions already installed on dev

| Runtime | Version/path |
|---|---|
| Node | `24.16.0` |
| npm/npx | `11.13.0` |
| Yarn | `4.5.3` from the repository release file |
| Python | `3.12.3` for the AI and current Ibis virtual environments |
| Java | Eclipse Temurin `21.0.11` |
| Java home | `C:\Program Files\Eclipse Adoptium\jdk-21.0.11.10-hotspot` |
| Maven | Bundled `3.9.15` under the Java engine directory |
| Qdrant executable | `1.15.0` |

Do not reinstall these runtimes for an ordinary source-only deployment.

## 2. WrenAI Service Flow

```text
Browser
  -> Wren UI (port 3000)
       -> Wren AI Service (port 5555)
            -> Qdrant vector store (port 6333)
       -> Wren Engine (port 8080)
       -> Ibis Server (port 8000)
            -> active project's external database
```

The UI also uses an internal Microsoft SQL Server database for WrenAI
application metadata. It stores projects, encrypted datasource settings,
models, relationships, threads, deploy records, and other application state.
This internal database is configured through `DB_TYPE` and `MSSQL_URL`; it is
not the same thing as a project's analytics datasource.

Never replace the UI's `MSSQL_URL` with an external analytics-database
connection. Changing the WrenAI metadata database is a separate, backup-and-
migration operation.

## 3. Multiple Database Connections

This custom branch supports multiple database connections through multiple
WrenAI projects. The older upstream README statement that only one project is
supported does not describe this branch's current project switcher.

### Connection model

- One WrenAI project owns one datasource type and one datasource connection.
- A workspace can contain multiple projects, so each database should normally
  have its own project.
- Only one project is marked `Current` at a time.
- Models, relationships, deploy state, dashboards, threads, instructions, and
  Ask history are scoped by project.
- Ask and query execution use the currently selected project. They do not join
  or search across separate projects automatically.
- Trino is the special case that accepts multiple comma-separated
  `catalog.schema` entries in one project and gathers tables from each entry.
- If databases require separate credentials, permissions, semantic models, or
  business ownership, keep them in separate projects even when they are hosted
  by the same database server.

The previously validated multi-project set on this branch was:

| Project | Datasource tables seen | Models selected |
|---|---:|---:|
| CWPay | 364 | 350 |
| CW_GL | 223 | 223 |
| Orders | 103 | 101 |
| PCB_DB | 76 | 68 |

These counts are a 2026-09-01 validation snapshot, not a promise that the
source schemas will never change. Connection types, hosts, users, passwords,
and other credential values are intentionally not included here.

### Supported external datasource types

Every form includes `displayName`. The following table lists the other current
form fields. Supply values only through the WrenAI UI or another approved
secret channel; never add real credentials to Git or this document.

| Datasource | Connection fields | Secret or credential fields |
|---|---|---|
| BigQuery | `projectId`, `datasetId`, `credentials` | `credentials` |
| PostgreSQL | `host`, `port`, `database`, `user`, `password`, `ssl` | `password` |
| MySQL | `host`, `port`, `database`, `user`, `password`, `ssl` | `password` |
| SQL Server | `host`, `port`, `database`, `user`, `password`, `trustServerCertificate` | `password` |
| Oracle | `host`, `port`, `database`, `user`, `password`, `dsn` | `password`, `dsn` |
| ClickHouse | `host`, `port`, `database`, `user`, `password`, `ssl` | `password` |
| Trino | `host`, `port`, `schemas`, `username`, `password`, `ssl` | `password` |
| Snowflake | `account`, `database`, `schema`, `warehouse`, `user`, `password`, `privateKey` | `password`, `privateKey` |
| Athena | `athenaAuthType`, `schema`, `s3StagingDir`, `awsRegion`, `awsAccessKey`, `awsSecretKey`, `webIdentityToken`, `roleArn`, `roleSessionName` | `awsAccessKey`, `awsSecretKey`, `webIdentityToken` |
| Redshift | `redshiftType`, `host`, `port`, `database`, `user`, `password`, `clusterIdentifier`, `awsRegion`, `awsAccessKey`, `awsSecretKey` | `password`, `awsAccessKey`, `awsSecretKey` |
| Databricks | `databricksType`, `serverHostname`, `httpPath`, `accessToken`, `clientId`, `clientSecret`, `azureTenantId` | `accessToken`, `clientSecret` |
| DuckDB | `initSql`, `configurations`, `extensions` | Treat credentials embedded in SQL/configuration as secrets |

Connection secrets are encrypted before being stored in WrenAI application
storage. That protection depends on the server's existing
`ENCRYPTION_PASSWORD` and `ENCRYPTION_SALT`. Do not rotate those values during
a routine deployment; existing encrypted connections could become unreadable.

### Before connecting a database

Obtain approval and prepare a least-privilege database account:

- prefer read-only access for the required databases, schemas, tables, and
  views
- allow network traffic from the dev server to the database host and port
- confirm DNS resolution, firewall rules, TLS/SSL requirements, and certificate
  policy
- confirm that the account can enumerate metadata and execute read-only preview
  queries
- use a unique project display name so the active connection is obvious
- decide which tables are actually required; selecting an entire large schema
  increases modeling and AI-indexing time
- never paste credentials into Git, Markdown, logs, tickets, or chat

### Add each database through the UI

Repeat this workflow once for every separate database connection:

1. Verify all five WrenAI health endpoints in section B6.
2. Open `http://ZUSEVRAIDEV01:3000/`.
3. Open the project/organization selector in the top-left navigation.
4. Select **New project**.
5. Choose the required project experience: **Classic** for the established
   modeling/Ask workflow, or **Agentic** only when that project type is intended.
6. Choose the datasource type.
7. Enter a unique display name and the approved connection fields.
8. Select **Next**. WrenAI creates the project, makes it current, and validates
   the connection by requesting the datasource's table metadata. A failed
   validation removes the incomplete project and displays the connection error.
9. On **Select Tables**, choose only the required tables/views and continue.
10. On **Define Relationships**, review detected relationships. Correct them,
    add approved relationships, or skip only when relationships are not needed.
11. Open **Modeling** and review table names, column types, primary keys,
    descriptions, calculated fields, and relationships.
12. Deploy the model and wait for deploy status `SUCCESS` and AI synchronization
    status `SYNCRONIZED` before using Ask.
13. Preview representative models and run database-specific Ask questions.
14. Switch to every other project and run a regression question to verify that
    project data and schema context remain isolated.

### Switch between existing database projects

1. Open the top-left project selector.
2. Search for and select the required project.
3. Confirm that it displays the `Current` badge.
4. Confirm the project name again before Modeling, SQL preview, Ask, settings,
   or deletion operations.

Switching a project clears the UI client cache and returns to Home. Start a new
Ask thread after switching when validating isolation. A question about another
project's tables should return an unsupported/clarification result such as
`NO_RELEVANT_SQL`, not SQL against the wrong schema.

### Updating an existing connection is destructive to project metadata

Changing datasource settings under project settings is not a harmless password
edit. The current implementation re-tests the connection and clears that
project's schema-change records, deploy records, Ask records, views, models, and
AI index so they can be rebuilt for the new connection.

Before changing an existing connection:

1. Confirm the `Current` project is the intended project.
2. Record/export the current modeling configuration through an approved method.
3. Back up the WrenAI metadata database.
4. Arrange a maintenance window.
5. Update the connection, select tables again, rebuild relationships/modeling,
   deploy, synchronize, and re-test Ask.

Create a new project instead when the old project must remain available.

### Multiple-database acceptance checks

For every project, record only non-secret results:

- project display name and datasource type
- connection test passed
- number of discovered and selected tables
- three representative model previews passed
- deploy status is `SUCCESS`
- AI sync status is `SYNCRONIZED`
- at least one count, grouping, date/latest, and same-thread follow-up Ask passed
- unsupported cross-project question did not leak schema or data

Never record connection values, credentials, tokens, private keys, or full
connection strings in the test evidence.

## 4. Existing Dev Configuration

These files already exist on dev and must be reused. The list contains paths
and variable names only.

| File | Purpose | Secret variable names |
|---|---|---|
| `D:\WrenAI\wren-ai-service\.env.dev` | Active AI runtime environment | `LLM_API_KEY`, `EMBEDDER_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` |
| `D:\WrenAI\wren-ai-service\config.yaml` | AI model/provider configuration | References `LLM_API_KEY`, `EMBEDDER_API_KEY` |
| `D:\WrenAI\wren-ui\.env` | UI application database and service endpoints | `MSSQL_URL`, `ENCRYPTION_PASSWORD` |
| `D:\WrenAI\wren-ui\.env.local` | Active UI overrides | `MSSQL_URL`, `ENCRYPTION_PASSWORD` |
| `D:\WrenAI\wren-engine\wren-engine\ibis-server\.env` | Ibis endpoints/cache settings | None currently detected |
| `D:\WrenAI\wren-engine\wren-engine\wren-core-legacy\docker\etc\config.properties` | Java engine runtime | None currently detected |

Important UI variable names include:

```text
DB_TYPE
MSSQL_URL
ENCRYPTION_PASSWORD
ENCRYPTION_SALT
WREN_ENGINE_ENDPOINT
IBIS_SERVER_ENDPOINT
WREN_AI_ENDPOINT
GENERATION_MODEL
TELEMETRY_ENABLED
```

Do not display their values and do not copy the dev secret files into a local
Git checkout.

## 5. Scheduled Tasks and Health Ports

| Order | Scheduled Task | Port/health endpoint |
|---:|---|---|
| 1 | `WrenAI 01 Qdrant` | `http://127.0.0.1:6333/healthz` |
| 2 | `WrenAI 02 Wren Engine` | `http://127.0.0.1:8080/v2/health` |
| 3 | `WrenAI 03 Ibis Server` | `http://127.0.0.1:8000/health` |
| 4 | `WrenAI 04 AI Service` | `http://127.0.0.1:5555/health` |
| 5 | `WrenAI 05 UI` | `http://127.0.0.1:3000/` |

`WrenAI Watchdog` checks these services about every five minutes. Disable it
only while intentionally stopping services, and always enable it afterward.

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

Use this ownership map to choose validation:

| Changed area | Main responsibility | Minimum validation |
|---|---|---|
| `wren-ai-service/src/...` | Ask, retrieval, SQL generation, AI indexing | Focused Pytest files plus one project Ask check |
| `wren-ui/src/...` | UI, GraphQL/API, projects, modeling, deploy orchestration | Type check, focused Jest test, affected UI flow |
| `wren-ui/migrations/...` | WrenAI internal metadata database | Migration on a disposable/local DB plus rollback review |
| `wren-engine/...` | Engine/Ibis submodule | Repository-specific Java/Python tests and a separately approved deployment |
| Documentation only | Handoff/runbook | Markdown review and `git diff --check` |

Focused Ask/schema tests used for this branch:

```powershell
Set-Location <existing-local-WrenAI-path>\wren-ai-service

.\venv\Scripts\python.exe -m pytest `
  tests/pytest/services/test_ask.py `
  tests/pytest/pipelines/generation/test_sql_schema_grounding.py `
  tests/pytest/pipelines/retrieval/test_db_schema_retrieval.py `
  -q
```

UI validation commands:

```powershell
Set-Location <existing-local-WrenAI-path>\wren-ui

node .\.yarn\releases\yarn-4.5.3.cjs check-types
node .\.yarn\releases\yarn-4.5.3.cjs test <affected-test-file> --runInBand
```

For multi-project or datasource work, manually verify creating/selecting a
project, table selection, modeling, deploy/sync, Ask, and cross-project
isolation. Do not claim a check passed when a pre-existing or new test failure
prevented it from running.

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
| `wren-ui/migrations/...` | Back up/migrate the metadata DB, then restart `WrenAI 05 UI` |
| Root service configuration/startup scripts | Review explicitly; restart only the affected task |
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

For full-stack recovery only, stop in reverse order and start in dependency
order:

```powershell
Disable-ScheduledTask -TaskName "WrenAI Watchdog"

Stop-ScheduledTask -TaskName "WrenAI 05 UI"
Stop-ScheduledTask -TaskName "WrenAI 04 AI Service"
Stop-ScheduledTask -TaskName "WrenAI 03 Ibis Server"
Stop-ScheduledTask -TaskName "WrenAI 02 Wren Engine"
Stop-ScheduledTask -TaskName "WrenAI 01 Qdrant"

Start-ScheduledTask -TaskName "WrenAI 01 Qdrant"
Start-ScheduledTask -TaskName "WrenAI 02 Wren Engine"
Start-ScheduledTask -TaskName "WrenAI 03 Ibis Server"
Start-ScheduledTask -TaskName "WrenAI 04 AI Service"
Start-ScheduledTask -TaskName "WrenAI 05 UI"

Enable-ScheduledTask -TaskName "WrenAI Watchdog"
```

Run the health checks below after a full restart. Do not use a full restart for
an ordinary one-service source change.

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
