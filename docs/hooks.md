# Hooks

User executables in `hooks/` run at phase boundaries. The **filename** determines when the script
runs; a non-zero exit aborts the action.

## Hook points

| File | Runs |
|------|------|
| `pre-run` | once, before the whole action |
| `post-run` | once, after the whole action succeeds |
| `<phase>.pre` | before a phase |
| `<phase>.post` | after that phase succeeds |
| `on-fail` | when a phase fails (in place of its `.post`) |

Phases: `preflight`, `install-tools`, `terraform`, `maas-setup`, `sunbeam`, `destroy`.

## Environment

Every hook receives:

- `SDB_PROVIDER` (`manual` / `maas`)
- `SDB_ACTION` (`up` / `plan` / `destroy` / `sunbeam`)
- `SDB_PHASE` (current phase; for `on-fail`, the failing phase)
- `SDB_HOOK` (the hook's filename)
- `SDB_CFG_<NAME>` for each configured flag (e.g. `SDB_CFG_MGMT_CIDR`)
- the inherited host environment

`on-fail` also receives the failed phase's combined stdout+stderr on **stdin**.

## Control

- `--hooks-dir` (default `hooks/`) — directory to scan for executable hooks.
- `--no-hooks` — disable hooks entirely.

## Examples

A cleanup hook that runs after a successful deploy:

```sh
#!/bin/sh
# hooks/post-run
lxc list
```

A failure notifier:

```sh
#!/bin/sh
# hooks/on-fail — reads the failed phase output from stdin
tee /tmp/last-failure.log | tail -20
logger -t sandbed "phase $SDB_PHASE failed"
```