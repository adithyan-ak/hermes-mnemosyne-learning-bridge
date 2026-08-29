# Hermes Mnemosyne Learning Bridge

[![CI](https://github.com/adithyan-ak/hermes-mnemosyne-learning-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/adithyan-ak/hermes-mnemosyne-learning-bridge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](pyproject.toml)

A project-aware memory provider for [Hermes Agent](https://github.com/NousResearch/hermes-agent) and [Mnemosyne](https://github.com/mnemosyne-oss/mnemosyne). It stores compact execution evidence instead of raw conversations, filters execution memories by project, and requires explicit confirmation before supported memory mutations are applied.

> [!WARNING]
> This is an experimental alpha. It deliberately exposes fewer mutation tools than the stock Mnemosyne provider and currently targets Python 3.11 plus a pinned Mnemosyne adapter revision. Use it with backups and test rollback before relying on it.

This project is independent and is not an official Nous Research or Mnemosyne project.

![Hermes sends deterministic tool evidence through project and policy checks before Mnemosyne stores a compact episode](docs/architecture.jpg)

## Why this exists

Raw transcript memory is noisy and risky. It can retain copied secrets, abandoned ideas, stale task state, and an agent's own unsupported claims. A useful execution memory should answer a narrower question: what happened, what deterministic evidence exists, and which project may recall it?

The bridge adds four controls:

- **Evidence-grounded episodes.** It records at most one compact episode from meaningful tool results. It does not copy user prompts, assistant prose, or raw tool output into the episode.
- **Project isolation.** Equivalent SSH and HTTPS Git remotes map to the same hashed project ID. Project episodes are filtered during explicit recall and silent prefetch.
- **Mutation approval.** Supported writes, updates, and deletes are staged with the exact review payload. Applying one requires an exact foreground `APPLY <id>` message and deterministic read-back.
- **Fail-closed policy.** Unknown and unsupported mutation families stay hidden or blocked.

## What it stores

An execution episode can contain:

- a hashed project identity;
- tool names and evidence categories;
- success or failure determined from reviewed tool-result shapes;
- file basenames, never full local paths;
- loaded skill names and versions;
- a deterministic fingerprint.

It does not intentionally store full transcripts, command output, file contents, session IDs, turn IDs, credentials, repository URLs, or absolute paths.

Secret detection is defense in depth, not a guarantee. Do not put credentials in agent memory or prompts.

## Install

The short version:

```bash
git clone https://github.com/adithyan-ak/hermes-mnemosyne-learning-bridge.git
cd hermes-mnemosyne-learning-bridge
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
if [ "$(command -v hermes)" = "/usr/local/bin/hermes" ] && [ -x /usr/local/lib/hermes-agent/venv/bin/python ]; then
  HERMES_PYTHON=/usr/local/lib/hermes-agent/venv/bin/python
else
  HERMES_PYTHON="$HERMES_HOME/hermes-agent/venv/bin/python"
fi
test -x "$HERMES_PYTHON"
uv pip install --python "$HERMES_PYTHON" .
```

Then follow the backup, Hermes configuration, canary, and rollback steps in [the installation guide](docs/INSTALL.md). Do not switch providers until the canary and restore path work.

## Safety model

The bridge expects Mnemosyne to be the sole durable fact store and raw role synchronization to be disabled. Initialization fails unless:

```yaml
memory:
  memory_enabled: false
  user_profile_enabled: false
  mnemosyne:
    sync_roles: []
```

Set these values with `hermes config set`; do not hand-edit the live Hermes configuration.

Only these mutation families are currently staged and supported:

- `mnemosyne_remember`
- `mnemosyne_update`
- `mnemosyne_forget`

The stage result and pending-list tool return the exact tool arguments plus a SHA-256 digest. Inspect that review payload before sending `APPLY <id>`. A claimed mutation is single-use; if application or read-back fails, stage a new mutation rather than replaying the old ID.

Canonical, graph, shared-memory, validation, synchronization, import/export, repair, and consolidation mutations remain blocked unless the operation is explicitly classified as read-only.

Read [the threat model](docs/THREAT_MODEL.md) before changing that policy.

## Compatibility

| Component | Public alpha baseline |
| --- | --- |
| Python | 3.11 |
| Hermes Agent | Official installer layout and the documented `hermes_agent.memory_providers` entry-point contract |
| Mnemosyne core | 3.15.1 |
| Mnemosyne Hermes adapter | Pinned public upstream commit in `pyproject.toml` |
| Operating system | Linux/POSIX |

The adapter pin is intentional because the required upstream integration has not reached a matching package release. No Hermes release range is certified yet; the installation guide requires live discovery, canary, isolation, and rollback checks. The provider also uses schema-coupled read-back logic that must be retested after Mnemosyne upgrades.

## Development

```bash
uv sync --locked --all-extras
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/mypy src
.venv/bin/python -m pytest
.venv/bin/python scripts/check_public_tree.py
.venv/bin/python -m build
.venv/bin/twine check dist/*
```

The test suite covers evidence extraction, secret rejection, Git remote normalization, project filtering, pending-record integrity, exact confirmation, deterministic read-back, maintenance, and a real local Mnemosyne integration path.

`pip-audit` checks packages available through its vulnerability sources. The pinned Git-based Mnemosyne adapter is not published as a matching PyPI distribution and is therefore reported as unauditable; the exact commit pin and integration tests are the current compensating controls.

## Documentation

- [Installation and rollback](docs/INSTALL.md)
- [Architecture and extension points](docs/ARCHITECTURE.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Security policy](SECURITY.md)
- [Support scope](SUPPORT.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [Design field note](https://adithyanak.com/optimal-hermes-mnemosyne-memory-architecture/)

## License

MIT. See [LICENSE](LICENSE).
