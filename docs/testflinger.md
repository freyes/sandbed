# Testflinger

`--use-testflinger` runs the requested action on a remote machine allocated from a Testflinger
queue, instead of locally:

1. allocate a node (or reuse an existing job),
2. wait until its `device_ip` appears, then until `ssh` to it is accepted (retried every 15 s for up
   to 5 min: a node can be up before its `authorized_keys` is populated),
3. remove any stale `~/sandbed/.venv` on the node, then `rsync` the local repo to `ubuntu@<ip>:~/sandbed` (excluding `.venv`, `__pycache__`, `*.pyc`,
   `.pytest_cache`, `.ruff_cache`, `.terraform`),
4. prepare the node and run `uv run sandbed <args>` on it (testflinger flags stripped). Both run
   detached under `nohup` on the node, so they survive a dropped ssh connection (see below).

The dedicated SSH key (`.sandbed_ed25519`, see [usage](usage.md#ssh-key)) lives in the working
directory, so step 3 copies its private half to the node, and the public half is appended to the
node's `~/.ssh/authorized_keys` (idempotent) between the sync and the host preparation.

## Prerequisites

- `testflinger-cli` must be in `$PATH` (snap `testflinger-cli`).
- You must already be authenticated: `testflinger-cli login`, or point at a server via the
  `TESTFLINGER_SERVER` environment variable.

## Flags

| Flag | Meaning |
|------|---------|
| `--use-testflinger` | enable remote execution (default off) |
| `--testflinger-series` | distro to provision (default `resolute`) |
| `--testflinger-queue` | queue name (**required** when `--use-testflinger`) |
| `--testflinger-timeout` | humanized reservation timeout, e.g. `6h` / `30m` / `45s` (default `6h`) |
| `--testflinger-job` | reuse an existing Testflinger job id instead of allocating |
| `--testflinger-job-file` | a Jinja2-templated job YAML to `submit` (see below) |
| `--testflinger-key` | SSH identity to install (`lp:user` / `gh:user`); repeatable, **required** when allocating a new node |

`--testflinger-job` and `--testflinger-job-file` are mutually exclusive.

## Job-file templating

With `--testflinger-job-file <file>`, the YAML is rendered through Jinja2 with these variables
before submission:

- `queue` — the `--testflinger-queue` value
- `series` — the `--testflinger-series` value
- `timeout` — `--testflinger-timeout` converted to seconds

```yaml
job_queue: {{ queue }}
provision_data:
  distro: {{ series }}
reserve_data:
  ssh_keys:
    - lp:youruser
  timeout: {{ timeout }}
```

## Notes

- The device is reached via `ubuntu@<device_ip>` with
  `-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null` (freshly reserved hosts
  present unknown keys).
- Reusing a job id (`--testflinger-job`) does not require `--testflinger-key`, since no new
  reservation is created.
- To end a reservation early: `testflinger-cli cancel <job_id>`.

## Reusing an allocation

When you run with `--use-testflinger` and request a *new* reservation (no `--testflinger-job` and
no `--testflinger-job-file`), the tool persists the allocated job id to
`$CWD/.sandbed.testflinger.job_id` and, on a later run, reuses that allocation instead of
submitting a fresh job — provided it is still live and on the requested queue:

- **Alive** means `testflinger-cli status <id>` reports one of `waiting`, `provision`, `test`, or
  `reserve`.
- **Queue must match**: the job's `job_queue` (from `testflinger-cli show <id>`) must equal
  `--testflinger-queue`.
- On reuse, if the reserved job's `reserve_data.timeout` differs from the requested
  `--testflinger-timeout`, a warning is printed (non-fatal) and the existing allocation is used
  as-is.

If the recorded job can't be reused (testflinger has no record of it, it is no longer alive, or it
is on a different queue), a warning naming the reason is printed, the dotfile is removed, and a
fresh job is submitted and recorded.

Precedence: `--testflinger-job <id>` and `--testflinger-job-file <path>` always win — they never
consult the dotfile; the latter overwrites it with the newly submitted id. The dotfile is *not*
auto-cleaned on `destroy` or expiry: the next implicit reserve simply re-submits and overwrites it.

## Surviving a dropped connection

The node preparation and the remote `sandbed` run are started detached (`nohup`, in
`~/.sandbed-jobs/<name>.{log,pid,rc}` on the node, names `prep-host` and `run`). The local sandbed
then follows the log over ssh, with keepalives. If the connection drops, the job keeps running on
the node; sandbed reconnects every 15 s for up to 5 min and resumes from the last complete line, so
no output is repeated. The exit status comes from the `.rc` file, so it is propagated even after a
reconnect.

Ctrl+C stops only the local follower: sandbed exits with status 130 and prints how to stop the job
(`ssh ubuntu@<ip> 'kill $(cat ~/.sandbed-jobs/run.pid)'`). The job on the node is not signalled and keeps
running. If the local sandbed is interrupted in any other way (closed laptop), the job still runs too. Run the same
command again: a job that is still running is attached to instead of started a second time, and a
finished one is started afresh. Inspect it by hand with `tail -f ~/.sandbed-jobs/run.log` on the node.

Not detached: the short setup commands (ssh probe, stale `.venv` removal, `authorized_keys`), and the
ssh calls that `sandbed` itself makes from the node to the client VM. The latter are protected
anyway when you use `--use-testflinger`, because the whole remote run is detached.

