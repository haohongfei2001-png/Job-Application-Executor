# Private native preparation child

The existing native routine-fill and separately approved resume-upload surface
now starts its owner in a dedicated local Python child. It does not add a final
request operation, reconnect the denied browser, accept a native dialog or enable
real applicant tests. Production launch has one fixed entrypoint and no selectable
fixture, module or arbitrary command.

## Authority and lifetime

The child owns the existing controller, durable queue authority, native browser
admission, driver and browser together. Every existing session/authority check
synchronously consults the parent over a separate inherited descriptor pair;
there is no cached heartbeat approval. The parent checks the original bound UI
session plus its current retirement and mutation fences. Sequence mismatch,
timeout, malformed response, failed check or pipe loss irreversibly revokes.

Cancellation has an independent lifetime pipe. Closing it does not wait behind
a blocked command or a session-manager lock. Parent death also closes the command
channel. The child requests cancellation without calling browser APIs from the
watcher thread; existing controller owner-thread cleanup proves browser and
driver absence. Parent CLOSED requires that explicit proof and child exit.
A crash, missing response or uncertain cleanup remains UNKNOWN and cannot be
used to open a replacement owner or replay a command. Natural terminal state
causes acknowledged child retirement before CLOSED is exposed to the manager.

## Private transport

The fixed interpreter uses isolated mode and no bytecode writes. Child-only
environment construction excludes recording/debug, Python/Node/loader injection
and unrelated service secrets while preserving proxy/trust and native runtime
paths. Parent environment and security/network configuration remain unchanged.
stdin, stdout and stderr are DEVNULL; raw errors and browser objects are never
serialized. Bounded sequence-checked JSON travels only through inherited pipes.
Routine-value offers still use the existing private UI path. Resume bytes stay
inside the child's original file-bound upload flow. Descriptors become
noninheritable before browser descendants start.

## Evidence boundary

Permanent tests use actual subprocesses for environment/canary isolation,
current-session revocation, wrong-session refusal, cancellation during a blocked
command, real parent-process death, partial/malformed frames, non-consuming
peers, guard timeout, and uncertain/natural closure. Hosted Mac additionally
runs the actual native browser admission and durable synthetic fill over this
process boundary, including revocation before any field write and proven owner
closure. Local Linux skips do not substitute for those hosted Mac results.

Final-request classifier/opaque bridge integration, physical-human confirmation,
CAPTCHA-stage support, real application acceptance, owner-device installation and
signing remain separate incomplete gates. Existing consent remains specific to
routine data and to each original resume file. Identity/protected fields,
agreements and final submission remain human-only. No test count or unsigned
artifact establishes certification or a completed application.
