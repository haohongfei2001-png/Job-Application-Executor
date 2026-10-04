# Partial lifecycle soak preparation

`scripts/run_lifecycle_soak.py` supplies the missing repeatable endurance
measurement for one bounded part of Z-04: the actual isolated local service over
a fixed synthetic paused queue. It does not close Z-04, even after a 24-hour run.
The acceptance matrix remains unchanged.

## Run scope

- A new mode-0700 workspace is mandatory. Existing paths and aliases are refused.
- HOME is isolated before importing the application, and inherited credentials,
  model configuration, local tokens and proxies are removed.
- The runner copies a manifested source snapshot and uses the existing working
  interpreter. It does not relocate Python or install a new runtime.
- All tasks are synthetic, explicitly not live-authorized and paused before the
  service starts. Their task identities, target/profile bindings, revisions,
  receipts and complete baseline snapshot are independently checked.
- The real owned child binds port 0. Every cycle checks authenticated identity,
  health, source digest, idle worker and zero browser/preparation/submit records.
- A completed cycle requires authenticated stop, the actual Popen child's exit,
  absent registry and worker-lock availability. Registry disappearance alone is
  insufficient. No process is adopted from a PID hint.

Example from the repository root, using a new path each time:

```sh
.venv/bin/python -I -B scripts/run_lifecycle_soak.py \
  --workspace /tmp/jae-synthetic-lifecycle-new-run \
  --duration-seconds 3600 --hold-seconds 60 --tasks 20
```

The default is a one-hour partial observation with 20 tasks and one-minute
service dwell intervals. `--duration-seconds 86400` requests an actual 24-hour
window; no simulated clock, resume, elapsed-time accumulation or acceptance
promotion is supported. A short smoke window is useful for checking the runner,
not duration evidence. This tool is intended for Linux/macOS engineering hosts.

## Evidence and limits

Each private, fsynced checkpoint has a sequence number, actual monotonic elapsed
time, completed cycles and health observations. Preparation has its own timestamp
and duration; the observation clock starts only after the synthetic queue and
independent baseline are ready, and preparation never contributes to elapsed-soak
evidence. Separate fields bind source
payload, dependency file, harness file, Git HEAD, dirty-checkout state, interpreter
version/binary digest and host OS/architecture. Each checkpoint also records the
exact cumulative bytes of all checkpoint files through itself. Dirty
work is disclosed and is not exact committed-head evidence. A copied source
snapshot never silently advances when the development checkout changes.

Linux samples measure the owned service's current RSS and open descriptors.
Other platforms explicitly report those measurements unavailable. Operational
ceilings are 512 MiB RSS and 128 descriptors; these are abort guards, not proof
of zero growth. Restarts reset child resources, and runner-process resource
trends are not measured. Reports are bounded to 50,000 checkpoints/128 MiB;
service logs are checked against 1 MiB each/16 MiB total. Observations and
operation deadlines are bounded; a process-level deadline is the requested duration plus 60 seconds total for
preparation/completion/cleanup overhead. Slow preparation can therefore cause an
explicit incomplete failure; it cannot shorten the required observation and pass.

Successful exit 0 plus final `COMPLETED_PARTIAL_WINDOW` means only this bounded
scope completed. Require both; a RUNNING, partial, unreadable or absent final
checkpoint is incomplete. `full_z04_pass` is always false and certification
always `NOT_CERTIFIED`. `continuous_24h_observed` can become true only after an
actual requested >=24h uninterrupted completed window. Service-running time and
health-observation counts are separate from elapsed time.

SIGINT/SIGTERM yields nonzero `INTERRUPTED` and keeps all evidence. Any invariant,
resource, source, startup or shutdown failure remains FAILED. Graceful cleanup
is attempted only for the directly owned child; if necessary, that exact child
can be terminated and reported as forced cleanup. Forced cleanup never counts
as a successful cycle. A surviving child is explicitly UNCONFIRMED and blocks
adoption of the run. Do not restart or combine runs to erase a failure.

The workspace contains generated local authentication tokens and answer keys.
**Never upload or distribute the workspace, runtime, service logs or database.**
Only value-free checkpoint/report JSON may be considered for an engineering
receipt after review; do not include raw private runtime files.

Missing coverage remains explicit: browser/task execution, server-draft results,
updates/rollback, crash recovery, work-bearing reconnection, native Mac behavior,
external sites, leak-trend acceptance and final consumer certification. No real
application, upload, account action or final submit is part of this runner.

## Regression contract

Black-box tests cover real repeated lifecycle, unchanged paused journals,
poisoned ambient HOME/settings/token/proxy isolation, private reports without
bearer contents, existing/aliased root refusal, invalid/nonfinite arguments,
SIGTERM, durable receipt/source corruption, wrong registry ownership, synthetic
resource-threshold canaries and too-short windows without observed cycles.
The complete runner file is selected in the existing Linux foundation and
hosted Mac runtime/distribution jobs, without a new runner allocation or changed
time budget. The existing production lifecycle, golden/state and final hosted
gates remain.

## Inspect a surviving checkpoint chain without resuming it

```sh
python3 -I -B scripts/inspect_lifecycle_soak.py /absolute/private/soak-workspace
```

The inspector is read-only and uses only matching checkpoint JSON. It never
opens runtime registries, credentials, keys, logs or databases, probes a service,
restarts a run, combines durations or performs cleanup. Private owned regular
files, contiguous sequence, exact cumulative bytes, fixed source/harness identity,
monotonic progress and consistent terminal claims must all validate. Aliases,
hardlinks, FIFOs, changed files, corrupt/truncated JSON and false certification
claims are refused. Directory/file/total-read budgets are finite.

- Exit 0 / `COMPLETED_PARTIAL_REPORTED`: an internally consistent terminal
  partial-completion report. This does not independently verify the original
  execution or its exit code and does not certify Z-04.
- Exit 2: missing terminal, reported failure/interruption or a reported observation
  gap. A stale `RUNNING` tail never becomes completion just because time passed.
- Exit 1 / `INVALID_EVIDENCE`: damaged, inconsistent or unadmitted input.

Freshness is separate from process state: a stale checkpoint does not prove why
an executor stopped, and a recent checkpoint does not prove a process is alive.
The default 300-second bound only labels stale records/large observation gaps;
it is not a product acceptance threshold. Actual process/exit evidence must be
checked separately when available. All outputs remain `NOT_CERTIFIED`, with
`execution_independently_verified: false` and `full_z04_pass: false`.

The adopted db39 observation demonstrates this case: its last 01:57:21 UTC
checkpoint remained RUNNING when inspected after 07:13, the execution session and
exact processes were gone, and no terminal report existed. Only 1,361.510 seconds
were observed. See the [sanitized interrupted receipt](receipts/LIFECYCLE-DB39-INTERRUPTED.md);
the unobserved hours were not accumulated, and the original evidence was not
rewritten or restarted.
