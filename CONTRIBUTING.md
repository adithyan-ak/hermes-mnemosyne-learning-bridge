# Contributing

Contributions are welcome, especially compatibility reports, adversarial tests, and smaller interfaces around upstream Mnemosyne behavior.

## Set up

```bash
git clone https://github.com/adithyan-ak/hermes-mnemosyne-learning-bridge.git
cd hermes-mnemosyne-learning-bridge
uv sync --locked --all-extras
```

## Before opening a pull request

```bash
.venv/bin/ruff check src tests scripts
.venv/bin/ruff format --check src tests scripts
.venv/bin/mypy src
.venv/bin/python -m pytest
.venv/bin/python scripts/check_public_tree.py
.venv/bin/python -m build
.venv/bin/twine check dist/*
```

Add a failing test before changing behavior. Keep fixtures synthetic. Never commit a memory database, transcript, runtime configuration, agent policy, deployment manifest, credential, host path, or backup archive.

## Scope

Good changes improve project identity, evidence validation, privacy, compatibility, mutation safety, or testability. Deployment-specific backup systems and personal agent configuration belong in separate private repositories.

## Reporting security problems

Do not open a public issue for a vulnerability that could expose memory contents or bypass approval controls. Follow [SECURITY.md](SECURITY.md).
