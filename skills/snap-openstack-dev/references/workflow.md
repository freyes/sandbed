# Workflow reference

## Fields to read

| Command | Read these fields |
|---|---|
| `create` | `lab_id`, `vm_address`, `vm_uuid` |
| `build` (blocking) | `artifact_id`, `artifact.sha256`, `artifact.source_head`, `artifact.source_content_sha256`, `artifact.adapter_qualified` |
| `deploy` | `artifact_id`, `snap_version`, `installed_sha256`, `already_deployed` |
| `test` | `status`, `failure_category`, `failed_stage`, `assertions`, `artifact`, `collection_status`, `report_path`, `diagnostics_path` |
| `status` | `provisioning_state`, `blocked_reason`, `busy`, `active_job`, `attempts[]` |
| `collect` | `collection_status`, `diagnostics_path`, `collection.entries[]` |

## Assertions of `bootstrap-and-launch` (version 1)

`kvm`, `bootstrap`, `authenticated`, `server_active`, `guest_ssh`, `cleanup`. `server_active`
alone is not success: `guest_ssh` proves the instance accepted SSH and finished cloud-init.

## Repeating commands

`deploy` with the already installed artifact only verifies it. `test` refuses a lab where
bootstrap was already attempted. `destroy` may be repeated and succeeds when nothing remains.
`collect` always creates a new collection attempt and does not rewrite the original result.

## Detached jobs

Launch envelope (any command with `--detach --json`, exit 0):

| Field | Meaning |
|---|---|
| `status` | `running`: the job started. Not a success. |
| `job_id` | Pass it to every job command below |
| `unit` | systemd user unit running the job |
| `job_path` | Job directory (job, result and exit records) |
| `stdout_path`, `stderr_path` | Log files, also readable with `sandbed dev job logs` |
| `linger` | `false`: the job stops when the last login session ends |
| `next` | Suggested follow-up command |

Job commands (all take `--job <job_id>` except `list`; all exit 0 when the inspection worked):

| Command | Options | Read these fields |
|---|---|---|
| `sandbed dev job status` | | `job.state`, `job.exit_code`, `job.service_result`, `job.progress`, `job.result` |
| `sandbed dev job wait` | `--timeout <s>` (default 300) | same as status, plus `timed_out` |
| `sandbed dev job logs` | `--stream stderr\|stdout`, `--offset`, `--max-bytes` | `data`, `next_offset`, `size`, `eof`, `finished` |
| `sandbed dev job cancel` | | same as status, plus `cancelled` (`false` with `reason` if already finished) |
| `sandbed dev job list` | `--active`, `--lab <id>`, `--limit <n>` | `jobs[]` with `job_id`, `command`, `lab_ref`, `state`, `created_at`, `unit` |

`job.state` values: `starting`, `running`, `succeeded`, `failed`, `interrupted`, `lost`. Only
`starting` and `running` mean keep waiting. `job.exit_code` comes from the recorded result and is
`null` until the job finishes. `job.progress` holds `current_stage` (set while running),
`attempts_tail[]` and `stderr` (`path`, `size`, `mtime`, `tail`). `job.result` is the envelope
the blocking command would have printed.
