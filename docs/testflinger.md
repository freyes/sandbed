# Testflinger

`--use-testflinger` runs the requested action on a remote machine allocated from a Testflinger
queue, instead of locally:

1. allocate a node (or reuse an existing job),
2. wait until its `device_ip` appears,
3. `scp` the local repo to `ubuntu@<ip>:~/sandbed`,
4. run `uv run sandbed <args>` on the remote node (testflinger flags stripped).

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