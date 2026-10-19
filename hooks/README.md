# Hooks

Place executable scripts in this directory. The filename determines when the script runs:

| File | Runs |
|------|------|
| `pre-run` | once, before the whole action |
| `post-run` | once, after the whole action succeeds |
| `<phase>.pre` | before a phase (`preflight`, `install-tools`, `terraform`, `maas-setup`, `sunbeam`, `destroy`) |
| `<phase>.post` | after that phase succeeds |
| `on-fail` | when any phase fails (in place of that phase's `.post`) |

A non-zero exit aborts the run. Hooks receive `SDB_PROVIDER`, `SDB_ACTION`, `SDB_PHASE`,
`SDB_HOOK`, and each configured flag as `SDB_CFG_<NAME>` on their environment. `on-fail` receives
the failed phase's combined stdout+stderr on stdin.

Enable/disable with `--hooks-dir` / `--no-hooks`.