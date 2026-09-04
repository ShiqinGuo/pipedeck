# Pipedeck

**Your GitLab pipeline, on your desktop.**

[English](README.md) · [简体中文](README.zh-CN.md)

[![Release](https://img.shields.io/github/v/release/ShiqinGuo/pipedeck)](https://github.com/ShiqinGuo/pipedeck/releases)
[![License: MIT](https://img.shields.io/github/license/ShiqinGuo/pipedeck)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-blue)](#requirements)

Tired of pushing commits just to find out your `.gitlab-ci.yml` fails? Pipedeck imports your repository, parses the pipeline you already wrote, and runs it for real on your machine — jobs with an `image` inside their container, everything else in the host shell — with run history, multi-project workspaces, and a CLI that drives the same control service as the GUI.

It is a local CI/CD client in the same spirit as [act](https://github.com/nektos/act) for GitHub Actions, but built for GitLab CI and shipping a desktop workbench instead of a terminal-only experience.

## Why

- `gitlab-runner exec` was removed in GitLab 17.0 — there is no official way to run a single job locally anymore.
- Terminal-only tools give you no run history, no failure aggregation, and no way to re-run a single job.
- Developers still hand-roll per-project scripts for variables, images, and multi-service deployments.

Pipedeck gives the whole path — repository → pipeline → execution → deployment — one local home.

## Features

- **Runs your actual `.gitlab-ci.yml`** — preview the full pipeline (jobs, stages, `needs`, images, expanded variables), then execute it: container jobs in their images, shell jobs on the host. Unsupported semantics are explicitly blocked with a reason, never silently degraded.
- **Run history you can replay** — per-job grouped logs, cancel, retry, and single-job re-runs.
- **Multi-project workspaces** — persist commands, environment variables, connection profiles, and run targets; versioned and re-runnable in one click.
- **Compose deployments with rollback** — build your source into immutable images, replace explicit Compose targets, verify readiness, and record recoverable deployment revisions.
- **Concurrent environments via git worktree** — every branch or tag gets its own worktree, Compose project, and deployment revision, side by side.
- **Secrets stay safe** — secret values live only in Windows Credential Manager; responses, logs, and the UI never echo them.
- **GUI and CLI over one control API** — every action panel shows the equivalent CLI command, and `pipedeck run --wait` fits scripts and scheduled tasks.
- **Protected cleanup** — cleanup only touches resources owned by Pipedeck; the last healthy instance of each middleware (PostgreSQL, Redis, Elasticsearch, MinIO) can never be deleted.

## Supported GitLab CI syntax

`stages`, `script` / `before_script` / `after_script`, `variables` (including local predefined `CI_*`), `rules:if` (subset), `needs`, `image`, `artifacts` (paths + `reports:dotenv`), `include` (local / remote / template, cached), `extends` / `!reference`, `workflow` / `default`, `allow_failure`, `when`.

Anything outside this subset — `trigger:project`, `pages`, OIDC/Vault secrets, `id_tokens`, Kubernetes — is blocked with an explanation instead of half-running.

`services`, `cache`, and `parallel:matrix` are on the roadmap.

## Requirements

- Windows 10 / 11 (x64)
- [Git](https://git-scm.com/download/win)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — for container jobs and Compose deployments

## Installation

Grab the latest installer from [GitHub Releases](https://github.com/ShiqinGuo/pipedeck/releases):

| File | Description |
| --- | --- |
| `Pipedeck_x.y.z_x64-setup.exe` | NSIS installer (recommended) — adds the `pipedeck` CLI to your `PATH` |
| `Pipedeck_x.y.z_x64_en-US.msi` | MSI package — suited for enterprise deployment |

Once installed, launch **Pipedeck** from the Start menu, or use the CLI right away:

```powershell
pipedeck status   # control plane / repositories / middleware summary
```

## Quick start

1. Open Pipedeck and import an existing checkout (or clone from GitLab) on the **Repositories** page.
2. Pipedeck parses `.gitlab-ci.yml` automatically — review what will run in the pipeline preview.
3. Hit **Run**. Jobs with an `image` execute in containers; you watch the same stages you know from GitLab.
4. Combine several repositories on the **Workspaces** page when you need multi-project runs.

## Development

```powershell
uv sync
corepack pnpm install

uv run pipedeck serve                      # control service on loopback :7421
corepack pnpm --filter @pipedeck/web dev   # web UI on 127.0.0.1:5173
```

Build the desktop app:

```powershell
corepack pnpm sidecar:build
corepack pnpm --filter @pipedeck/web build
corepack pnpm desktop:build   # artifacts land in apps/desktop/src-tauri/target/release/bundle/
```

Quality gates:

```powershell
uv run ruff format --check . ; uv run ruff check . ; uv run pyright ; uv run pytest
corepack pnpm --filter @pipedeck/web lint
corepack pnpm --filter @pipedeck/web typecheck
corepack pnpm --filter @pipedeck/web test
```

The UI ships in Simplified Chinese and English (switch under **Settings → Language**); add new copy to the locale partitions under `apps/web/src/i18n/locales/`.

Further design docs live in [`docs/`](docs/README.md) — product scope, domain model, interaction spec, and decision records.

## Contributing

Issues and pull requests are welcome at [github.com/ShiqinGuo/pipedeck](https://github.com/ShiqinGuo/pipedeck). Please run the quality gates above before submitting.

## License

Copyright (c) 2026 ShiqinGuo

Released under the [MIT License](LICENSE). Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
