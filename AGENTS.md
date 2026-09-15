# WrenAI Workspace Instructions

Before working in this repository, read these handoff documents completely:

- `DEV_SERVER_LOCAL_WORKFLOW_HANDOFF.md`
- `WRENAI_LOCAL_ASK_HANDOFF.md`

Unless the user explicitly changes the workflow, follow these rules:

- Make source changes, commits, and pushes from a clean local clone or clean
  integration worktree, not from the live `D:\WrenAI` dev checkout.
- Use branch `organization/ask-schema-grounding-20260820` as the shared source
  branch.
- On the dev server, only fetch, fast-forward, install/build when required,
  restart the affected Scheduled Tasks, and verify health endpoints.
- Record the pre-deployment SHA before changing the live checkout.
- Disable `WrenAI Watchdog` while intentionally stopping services and re-enable
  it afterward.
- Never run `git clean` in `D:\WrenAI`.
- Do not run `git submodule update`, `git submodule sync`, or reset the
  `wren-engine` submodule on dev. Its checkout is intentionally non-standard and
  its service paths depend on the current doubled directory layout.
- Do not commit or copy `.env*`, local YAML configuration, credentials, logs,
  virtual environments, `node_modules`, databases, Qdrant data, downloaded
  binaries/toolchains, `.codex-tmp`, or other runtime artifacts.
- Never print or commit passwords, API keys, tokens, database passwords, or full
  secret connection strings. When inspecting configuration, report paths and
  variable names only.
- Treat the root `wren-engine` modification and the large untracked-file set as
  expected local dev-server state; do not include them in application commits.

Current dev-server operational details, task names, versions, config paths,
health endpoints, deployment steps, and rollback commands are maintained in
`DEV_SERVER_LOCAL_WORKFLOW_HANDOFF.md`.
