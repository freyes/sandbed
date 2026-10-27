# Workflow reference

## Fields to read

| Command | Read these fields |
|---|---|
| `create` | `lab_id`, `vm_address`, `vm_uuid` |
| `build --detach` | `status` (`running`), `run_id`, `log_path`, `result_path`; then poll `status` |
| `build` (blocking) | `artifact_id`, `artifact.sha256`, `artifact.source_head`, `artifact.source_content_sha256`, `artifact.adapter_qualified` |
| `deploy` | `artifact_id`, `snap_version`, `installed_sha256`, `already_deployed` |
| `test` | `status`, `failure_category`, `failed_stage`, `assertions`, `artifact`, `collection_status`, `report_path`, `diagnostics_path` |
| `status` | `provisioning_state`, `blocked_reason`, `busy`, `last_run.{run_id,kind,state,artifact_id,result}`, `attempts[]` |
| `collect` | `collection_status`, `diagnostics_path`, `collection.entries[]` |

## Assertions of `bootstrap-and-launch` (version 1)

`kvm`, `bootstrap`, `authenticated`, `server_active`, `guest_ssh`, `cleanup`. `server_active`
alone is not success: `guest_ssh` proves the instance accepted SSH and finished cloud-init.

## Repeating commands

`deploy` with the already installed artifact only verifies it. `test` refuses a lab where
bootstrap was already attempted. `destroy` may be repeated and succeeds when nothing remains.
`collect` always creates a new collection attempt and does not rewrite the original result.
