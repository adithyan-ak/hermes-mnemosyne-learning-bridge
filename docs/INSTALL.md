# Installation and rollback

This alpha is distributed from GitHub, not PyPI. The commands below support the standard Hermes shell-installer layout. Other layouts must install the package into the same Python environment that launches Hermes; a repository-local virtual environment is not sufficient.

## Prerequisites

- Linux or another POSIX environment
- Python 3.11
- `git` and `uv`
- Hermes installed by the official shell installer
- A provider-native backup of the current Mnemosyne database
- The current Hermes memory settings copied somewhere outside this repository

Run the health check and record all four values before changing anything:

```bash
hermes doctor
hermes config get memory.provider
hermes config get memory.memory_enabled
hermes config get memory.user_profile_enabled
hermes config get memory.mnemosyne.sync_roles
```

If `hermes config get` reports that a key is unset, record it as unset. Rollback must restore that state rather than inventing a value.

## Install into the Hermes environment

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

Confirm that the interpreter used by Hermes can discover the package entry point:

```bash
"$HERMES_PYTHON" - <<'PY'
from importlib.metadata import entry_points
from mnemosyne_learning_bridge.provider import ProjectAwareMnemosyneProvider

matches = [
    point
    for point in entry_points(group="hermes_agent.memory_providers")
    if point.name == "mnemosyne-learning-bridge"
]
assert len(matches) == 1, matches
register = matches[0].load()
assert callable(register)
assert ProjectAwareMnemosyneProvider().name == "mnemosyne-learning-bridge"
print(matches[0].name, matches[0].value)
PY
```

Stop if the assertion fails. Do not copy modules into `site-packages` by hand.

## Configure Hermes

The bridge rejects duplicate durable authorities, raw role synchronization, and upstream automatic consolidation. Set the required values through the Hermes CLI:

```bash
hermes config set memory.memory_enabled false
hermes config set memory.user_profile_enabled false
hermes config set memory.mnemosyne.sync_roles '[]'
hermes config set memory.provider mnemosyne-learning-bridge
```

Restart the Hermes process or gateway that will use the provider. A new process must report the bridge as active and expose its tools:

```bash
hermes config get memory.provider
hermes doctor
hermes tools list
```

Check that:

- `memory.provider` is `mnemosyne-learning-bridge`;
- `hermes doctor` reports the same provider as active;
- the tool list contains `mnemosyne_bridge_pending_list`, `mnemosyne_bridge_apply_pending`, and `mnemosyne_bridge_reject_pending`.

`hermes memory status` alone is not proof that the package entry point loaded.

## Run a synthetic canary

Use a disposable value that contains no credentials, personal facts, customer data, private paths, or repository names.

1. In a fresh Hermes session, state a unique synthetic fact naturally. The resulting ordinary global write must quote that fact verbatim and use `source="user"`, `veracity="stated"`, `extract=false`, and `extract_entities=false`.
2. Confirm that `mnemosyne_remember` returns `status="stored"` or `status="deduplicated"`, `verified=true`, and an exact deterministic `readback`. It must not create a pending record.
3. Send an intentionally incomplete synthetic write and confirm that it returns `status="clarification_required"` without writing or staging anything.
4. Start another Hermes process in the same repository and recall the canary using different wording.
5. Produce one synthetic, deterministic execution episode in that repository. Confirm it can be recalled in the same repository and is absent from a different repository.
6. Stage deletion of the canary, inspect the exact review payload, send a new foreground message containing only `APPLY <pending_id>`, and verify that recall returns nothing.
7. Run `hermes doctor` and the available Mnemosyne integrity diagnostics.

Pending IDs are single-use once claimed. If mutation application or read-back fails, inspect the error and stage a new mutation. Do not replay the old ID.

## Roll back

Restore every setting changed during cutover, using the values recorded in the prerequisite step:

```bash
hermes config set memory.provider '<previous-provider>'
hermes config set memory.memory_enabled '<previous-memory-enabled>'
hermes config set memory.user_profile_enabled '<previous-user-profile-enabled>'
hermes config set memory.mnemosyne.sync_roles '<previous-sync-roles>'
```

For a key that was previously unset, use `hermes config unset <key>` instead of `set`.

Restart Hermes, then verify the restored state:

```bash
hermes config get memory.provider
hermes config get memory.memory_enabled
hermes config get memory.user_profile_enabled
hermes config get memory.mnemosyne.sync_roles
hermes doctor
```

Only after the previous provider is active may you remove the bridge package:

```bash
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
if [ "$(command -v hermes)" = "/usr/local/bin/hermes" ] && [ -x /usr/local/lib/hermes-agent/venv/bin/python ]; then
  HERMES_PYTHON=/usr/local/lib/hermes-agent/venv/bin/python
else
  HERMES_PYTHON="$HERMES_HOME/hermes-agent/venv/bin/python"
fi
uv pip uninstall --python "$HERMES_PYTHON" hermes-mnemosyne-learning-bridge
```

Do not delete bridge-created memories during the provider switch. Restore the database only if integrity checks fail and only from a verified provider-native backup.

## Upgrade

Before upgrading this bridge, Hermes, or Mnemosyne:

1. Back up the Mnemosyne database.
2. Record the installed bridge version and dependency commits.
3. Run the test suite and a same-project/foreign-project recall baseline.
4. Install the upgrade into an isolated copy of the Hermes environment.
5. Re-run entry-point discovery, initialization, canary, isolation, approval, integrity, and rollback tests.

The bridge depends on a pinned public Mnemosyne adapter commit and schema-coupled read-back behavior. Do not replace the commit pin with a floating branch.
