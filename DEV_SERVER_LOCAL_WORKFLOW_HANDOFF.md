# WrenAI Local Development and Dev Deployment Handoff

Audit date: 2026-09-15 UTC

This document describes the current Windows dev-server layout and the workflow
for making changes locally, pushing them, and manually deploying them to the dev
server. It intentionally contains no passwords, API keys, tokens, or database
connection-string values.

## Critical Current-State Warnings

The live dev checkout is not currently ready for a normal `git pull --ff-only`:

- Live root branch: `organization/ask-schema-grounding-20260820`
- Live root HEAD: `5179591dff4356cca36bc2417fd43d4e86856a39`
- Relative to its cached origin ref at audit time: ahead 2, behind 90
- Server-only commits:
  - `5179591df Optimize schema-grounded Ask pipeline`
  - `0ff1e6e23 Improve Ask schema grounding`
- Remote branch tip at audit time: `67dd42f984d3524e51fd336d765740776592a91c`
- The live checkout contains tracked changes and more than 100,000 untracked
  runtime/build files.

The `wren-engine` layout on dev is also non-standard:

```text
D:\WrenAI
└── wren-engine                 Another WrenAI fork checkout
    └── wren-engine             Actual Canner wren-engine checkout
        └── ibis-server
```

A clean local clone normally has the actual engine directly under
`<local-root>\wren-engine`. Do not reproduce the doubled nesting locally. Do not
run `git submodule update` on dev until its scheduled-task paths have been
normalized.

## 1. Dev Server Paths

| Component | Path |
|---|---|
| WrenAI root | `D:\WrenAI` |
| Wren AI Service | `D:\WrenAI\wren-ai-service` |
| Wren UI | `D:\WrenAI\wren-ui` |
| Outer engine checkout | `D:\WrenAI\wren-engine` |
| Actual Wren Engine source/runtime | `D:\WrenAI\wren-engine\wren-engine` |
| Java legacy engine | `D:\WrenAI\wren-engine\wren-engine\wren-core-legacy` |
| Ibis Server | `D:\WrenAI\wren-engine\wren-engine\ibis-server` |
| Qdrant checkout/binary | `D:\WrenAI\qdrant` |
| Qdrant executable | `D:\WrenAI\qdrant\qdrant.exe` |
| Active Qdrant data | `D:\WrenAI\storage` |
| Startup scripts | `D:\WrenAI\scripts` |
| Logs | `D:\WrenAI\logs` |

`wren-ai-service` and `wren-ui` belong to the root WrenAI repository; they are
not independent Git repositories.

## 2. Git Inventory

### Root WrenAI repository

- Remote: `https://github.com/hbalasubramanya-rgb/WrenAI.git`
- Branch: `organization/ask-schema-grounding-20260820`
- Audited HEAD: `5179591dff4356cca36bc2417fd43d4e86856a39`
- Status: dirty
- Tracked changes:
  - mismatched `wren-engine` submodule worktree
  - executable-mode-only change to
    `wren-ui/.yarn/releases/yarn-4.5.3.cjs`

### Outer engine checkout

- Path: `D:\WrenAI\wren-engine`
- Remote: `https://github.com/snjkmrd233etag/WrenAI.git`
- Branch: `main`
- HEAD: `8c20d279bc12b60e936f27934f2f16fe7b7af8d4`
- Status: dirty and behind its cached origin by 3

This is another WrenAI checkout even though it occupies the root repository's
engine-submodule path.

### Actual engine repository

- Path: `D:\WrenAI\wren-engine\wren-engine`
- Remote: `https://github.com/Canner/wren-engine.git`
- Branch: `main`
- HEAD: `35a8eb456260f8c4b8085fd5b1ed184ba79d3188`
- Status: dirty
- Material local change: `ibis-server/pyproject.toml` widens Python support from
  `<3.12` to `<3.13`
- Other changes include Windows mode/EOL churn, a deleted Maven extension file,
  modified lock data, and untracked build environments.

### Qdrant repository

- Remote: `https://github.com/qdrant/qdrant.git`
- Branch: `master`
- HEAD: `44ad62f8cd69642be5afa6441612525e24a0d063`
- Source commit describes Qdrant 1.18.2, but the running executable is Qdrant
  1.15.0.
- Two tracked generated Rust files have EOL-only changes.

### Local-only files that must not be committed

Some of the following are ignored, but many are not:

- `.env`, `.env.*`, service-local YAML configuration, and secret-bearing startup
  scripts
- `venv`, `.venv`, `node_modules`, `.next`, `.codex-tmp`
- logs, snapshots, test output, registries, and vendored toolchains
- `storage` and all Qdrant data directories
- SQLite files, backups, and `wrenai_full_dump.sql`
- `qdrant.exe`, `qdrant.zip`, downloaded Maven/Rust/Cargo distributions
- `wren-ui/package-lock.json`; this UI uses Yarn and `yarn.lock`
- local migration, extraction, benchmark, and diagnostic helpers

Never run `git clean` in the live checkout. It could remove virtual
environments, startup scripts, data, and configuration.

## 3. Current Service Startup Commands

The services are boot-triggered Windows Scheduled Tasks:

```powershell
Start-ScheduledTask -TaskName "WrenAI 01 Qdrant"
Start-ScheduledTask -TaskName "WrenAI 02 Wren Engine"
Start-ScheduledTask -TaskName "WrenAI 03 Ibis Server"
Start-ScheduledTask -TaskName "WrenAI 04 AI Service"
Start-ScheduledTask -TaskName "WrenAI 05 UI"
```

### Qdrant

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "D:\WrenAI\scripts\start-qdrant.ps1"
```

The script currently locates and launches
`D:\WrenAI\qdrant\qdrant.exe` while its working directory is `D:\WrenAI`.

### Wren Engine

```powershell
Set-Location D:\WrenAI\wren-engine\wren-engine\wren-core-legacy

& ".\apache-maven-3.9.15\bin\mvn.cmd" `
  -pl :wren-server `
  exec:java `
  "-Dexec.mainClass=io.wren.server.WrenServer" `
  "-Dconfig=docker/etc/config.properties" `
  "-Dnode.environment=production"
```

### Ibis Server

```powershell
Set-Location D:\WrenAI\wren-engine\wren-engine\ibis-server
$env:WREN_ENGINE_ENDPOINT = "http://127.0.0.1:8080"

.\venv\Scripts\python.exe -m uvicorn app.main:app `
  --host 0.0.0.0 --port 8000
```

### Wren AI Service

```powershell
Set-Location D:\WrenAI\wren-ai-service

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONLEGACYWINDOWSSTDIO = "0"

.\venv\Scripts\python.exe -m uvicorn src.__main__:app `
  --host 0.0.0.0 --port 5555 --loop asyncio
```

### Wren UI

The active scheduled task directly runs this command; it does not call
`scripts\start-wren-ui.ps1`:

```powershell
Set-Location D:\WrenAI\wren-ui
$env:TZ = "UTC"
npx next dev -p 3000
```

The `WrenAI Watchdog` scheduled task checks the five service ports about every
five minutes and restarts missing services. Disable it during deployments.

## 4. Runtime Versions

| Runtime | Dev server value |
|---|---|
| Node | `24.16.0` |
| npm/npx | `11.13.0` |
| Corepack | `0.35.0` |
| UI package manager | Yarn `4.5.3` |
| Python/system | `3.12.3` |
| AI venv Python | `3.12.3` |
| Ibis venv Python | `3.12.3` |
| Java | Eclipse Temurin OpenJDK `21.0.11` |
| Java path | `C:\Program Files\Eclipse Adoptium\jdk-21.0.11.10-hotspot` |
| Maven | `3.9.15` |
| Qdrant executable | `1.15.0` |

Compatibility notes:

- UI documentation specifies Node 18, although dev currently runs Node 24.16.0.
- AI Service declares Python `>=3.12,<3.13`.
- The clean Ibis checkout and lock require Python `>=3.11,<3.12`; use Python
  3.11 locally. Dev has an uncommitted override permitting Python 3.12.
- AI Service documentation specifies Poetry 1.8.3 and Just 1.36+.
- Poetry, Just, system Maven, Rust, and Cargo are not globally installed on dev.

## 5. Configuration Files

No values from these files should be committed or pasted into tickets/chat.

| File | Required by native services | Secret variable names |
|---|---|---|
| `D:\WrenAI\wren-ai-service\.env.dev` | Yes | `LLM_API_KEY`, `EMBEDDER_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` |
| `D:\WrenAI\wren-ai-service\config.yaml` | Yes | References `LLM_API_KEY`, `EMBEDDER_API_KEY` |
| `D:\WrenAI\wren-ai-service\.env` | Other workflows only | `OPENAI_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` |
| `D:\WrenAI\wren-ui\.env` | Yes | `MSSQL_URL`, `ENCRYPTION_PASSWORD` |
| `D:\WrenAI\wren-ui\.env.local` | Yes; overrides `.env` | `MSSQL_URL`, `ENCRYPTION_PASSWORD` |
| `D:\WrenAI\wren-engine\wren-engine\ibis-server\.env` | Yes | None detected |
| `D:\WrenAI\wren-engine\wren-engine\wren-core-legacy\docker\etc\config.properties` | Yes | None |
| `D:\WrenAI\qdrant\config\config.yaml` | Optional for current command | None active detected |
| `D:\WrenAI\.env` | Docker/launcher workflows | `OPENAI_API_KEY`, `POSTHOG_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` |

`D:\WrenAI\scripts\start-wren-ui.ps1` contains the variable
`MSSQL_PASSWORD`. It is not used by the current UI task but remains a
secret-bearing local file.

Transfer required secret files through an approved secure channel, or recreate
them locally with authorized local credentials. Never transfer them through
Git.

## A. Local Machine Setup

### A1. Clone the source-of-truth branch

Use the remote branch tip for new work rather than the live dev server's
divergent HEAD:

```powershell
git clone --branch organization/ask-schema-grounding-20260820 `
  --single-branch `
  https://github.com/hbalasubramanya-rgb/WrenAI.git `
  WrenAI

Set-Location .\WrenAI
```

If GitHub SSH is configured:

```powershell
git submodule update --init --recursive
```

To use HTTPS for the SSH-form submodule URL:

```powershell
git -c 'url.https://github.com/.insteadOf=git@github.com:' `
  submodule update --init --recursive
```

The clean local engine paths will be:

```text
<local-root>\wren-engine
<local-root>\wren-engine\ibis-server
<local-root>\wren-engine\wren-core-legacy
```

### A2. Wren AI Service environment

```powershell
Set-Location <local-root>\wren-ai-service

py -3.12 -m venv venv
& .\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install poetry==1.8.3
python -m poetry install

Copy-Item .\tools\config\.env.dev.example .\.env.dev
Copy-Item .\tools\config\config.example.yaml .\config.yaml
```

Populate the generated files with authorized local settings. Do not commit
them.

### A3. Ibis environment

Use Python 3.11 with the clean checkout:

```powershell
Set-Location <local-root>\wren-engine\ibis-server

py -3.11 -m venv venv
& .\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install poetry==1.8.3
python -m poetry install
```

Create `ibis-server\.env` with these variable names:

```text
WREN_ENGINE_ENDPOINT
WREN_WEB_ENDPOINT
QUERY_CACHE_STORAGE_TYPE
PROFILING_STORE_PATH
```

### A4. Java engine build

Install JDK 21 and Maven 3.9.15, then:

```powershell
Set-Location <local-root>\wren-engine\wren-core-legacy
mvn clean install -DskipTests
```

### A5. UI dependencies and database

Use Node 24.16.0 for the closest dev-runtime match, or Node 18 for the
repository-documented version:

```powershell
Set-Location <local-root>\wren-ui

corepack enable
node .\.yarn\releases\yarn-4.5.3.cjs install --immutable
```

Create `wren-ui\.env.local` with the required variables, including:

```text
DB_TYPE
MSSQL_URL
ENCRYPTION_PASSWORD
ENCRYPTION_SALT
WREN_ENGINE_ENDPOINT
IBIS_SERVER_ENDPOINT
WREN_AI_ENDPOINT
EXPERIMENTAL_ENGINE_RUST_VERSION
GENERATION_MODEL
TELEMETRY_ENABLED
```

Use a separate local database where possible. Do not unintentionally run local
migrations against the shared dev database.

```powershell
node .\.yarn\releases\yarn-4.5.3.cjs migrate
```

### A6. Start local services in order

Run each service in a separate terminal.

1. Qdrant 1.15.0:

   ```powershell
   qdrant.exe
   ```

   A `qdrant/qdrant:v1.15.0` container exposing ports 6333 and 6334 is also
   suitable.

2. Java engine:

   ```powershell
   Set-Location <local-root>\wren-engine\wren-core-legacy

   mvn -pl :wren-server exec:java `
     "-Dexec.mainClass=io.wren.server.WrenServer" `
     "-Dconfig=docker/etc/config.properties" `
     "-Dnode.environment=production"
   ```

3. Ibis:

   ```powershell
   Set-Location <local-root>\wren-engine\ibis-server
   .\venv\Scripts\python.exe -m uvicorn app.main:app `
     --host 0.0.0.0 --port 8000
   ```

4. AI Service:

   ```powershell
   Set-Location <local-root>\wren-ai-service
   .\venv\Scripts\python.exe -m uvicorn src.__main__:app `
     --host 0.0.0.0 --port 5555 --loop asyncio
   ```

5. UI:

   ```powershell
   Set-Location <local-root>\wren-ui
   $env:TZ = "UTC"
   node .\.yarn\releases\yarn-4.5.3.cjs dev
   ```

### A7. Local health checks

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:6333/healthz
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8080/v2/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5555/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:3000/
```

All five returned HTTP 200 on dev during the audit. The Java health route is
`/v2/health`; `/health` on port 8080 is not the correct health endpoint.

## B. Manual Dev Deployment

### B1. One-time branch reconciliation

Perform this once before the first deployment under the new workflow:

```powershell
Set-Location D:\WrenAI

git fetch origin
git branch backup/dev-before-local-workflow-20260915 HEAD

git log --oneline origin/organization/ask-schema-grounding-20260820..HEAD
git status --short --branch
```

Confirm that the two server-only commits have been superseded or otherwise
preserved on the remote branch. Then suppress Windows executable-bit churn:

```powershell
git config core.fileMode false
```

Disable the watchdog and stop the source services:

```powershell
Disable-ScheduledTask -TaskName "WrenAI Watchdog"
Stop-ScheduledTask -TaskName "WrenAI 05 UI"
Stop-ScheduledTask -TaskName "WrenAI 04 AI Service"
```

Align the root branch:

```powershell
git reset --hard origin/organization/ask-schema-grounding-20260820
```

This discards tracked root changes, but the previous HEAD is preserved by the
backup branch. It does not delete ignored/untracked configuration or data.

Do not run `git clean` or `git submodule update`.

Restart:

```powershell
Start-ScheduledTask -TaskName "WrenAI 04 AI Service"
Start-ScheduledTask -TaskName "WrenAI 05 UI"
Enable-ScheduledTask -TaskName "WrenAI Watchdog"
```

### B2. Normal deployment after a local push

```powershell
Set-Location D:\WrenAI

$Branch = "organization/ask-schema-grounding-20260820"

git fetch origin
git status --short --branch
git merge-base --is-ancestor HEAD "origin/$Branch"

if ($LASTEXITCODE -ne 0) {
    throw "Dev HEAD is not a fast-forward ancestor of origin/$Branch"
}

$PreviousCommit = (git rev-parse HEAD)
$PreviousCommit
git diff --name-only HEAD "origin/$Branch"
```

Record `$PreviousCommit` outside the repository for rollback.

```powershell
Disable-ScheduledTask -TaskName "WrenAI Watchdog"

Stop-ScheduledTask -TaskName "WrenAI 05 UI"
Stop-ScheduledTask -TaskName "WrenAI 04 AI Service"

git pull --ff-only origin $Branch
```

If AI dependency files changed:

```powershell
Set-Location D:\WrenAI\wren-ai-service
& .\venv\Scripts\Activate.ps1
python -m pip install poetry==1.8.3
python -m poetry install --only main
deactivate
```

If UI dependency files changed:

```powershell
Set-Location D:\WrenAI\wren-ui
node .\.yarn\releases\yarn-4.5.3.cjs install --immutable
```

If UI migration files changed:

```powershell
node .\.yarn\releases\yarn-4.5.3.cjs migrate
```

No UI production build is currently required because the task runs `next dev`.
Source-only AI changes require only a restart.

If Java engine code was updated separately:

```powershell
Set-Location D:\WrenAI\wren-engine\wren-engine\wren-core-legacy

& ".\apache-maven-3.9.15\bin\mvn.cmd" `
  clean install -DskipTests
```

Engine/Ibis Git deployment should not be automated until their dirty nested
checkout is normalized.

Restart AI/UI after an ordinary root deployment:

```powershell
Start-ScheduledTask -TaskName "WrenAI 04 AI Service"
Start-ScheduledTask -TaskName "WrenAI 05 UI"
Enable-ScheduledTask -TaskName "WrenAI Watchdog"
```

For a full restart, stop in reverse order and start in dependency order:

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

Verify with the health checks above. Browser URLs:

- On dev: `http://localhost:3000/`
- Hostname: `http://ZUSEVRAIDEV01:3000/`
- Audited IP: `http://10.104.74.13:3000/`

## B3. Rollback

Disable the watchdog and stop affected services:

```powershell
Disable-ScheduledTask -TaskName "WrenAI Watchdog"
Stop-ScheduledTask -TaskName "WrenAI 05 UI"
Stop-ScheduledTask -TaskName "WrenAI 04 AI Service"
```

Return to the SHA recorded before deployment:

```powershell
Set-Location D:\WrenAI
git reset --hard $PreviousCommit
```

If dependency lock files changed, reinstall dependencies from the restored
commit. Then restart and run all health checks:

```powershell
Start-ScheduledTask -TaskName "WrenAI 04 AI Service"
Start-ScheduledTask -TaskName "WrenAI 05 UI"
Enable-ScheduledTask -TaskName "WrenAI Watchdog"
```

If a UI database migration was applied, run the migration rollback only after
confirming that the migration is reversible and a database backup exists:

```powershell
Set-Location D:\WrenAI\wren-ui
node .\.yarn\releases\yarn-4.5.3.cjs rollback
```

The SHA reset is a dev-server-only rollback. For a permanent shared rollback,
create a `git revert` commit locally, push it, and deploy that new commit through
the normal fast-forward procedure.
