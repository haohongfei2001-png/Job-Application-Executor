# Deny-proxy oracle result and remaining fixture gaps

Exact head: `95d3fa83d1ad3c4e2e30a1de7eb5a7625349ad7e`.
Run: https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36850740266

Mac early oracle: **387 passed, 2 failed, 1 xfailed, 1 xpassed** in334.61s.
The installed headful Mac Chrome and bundled headless browser both passed native
HTTP/HTTPS/WS/WSS destination sinks over127.0.0.1,localhost and IPv6; native
worker transports, live/closed proxy, and independently positive/negative STUN
UDP and TURN TCP cases passed. Guarded controller and Node-driver death cases
passed. The raw-CDP comparator again observed the known pending-request escape;
its other timing case did not reproduce it. Neither comparator certifies CDP.

Both WebTransport positive controls failed because the unfenced browser never
reached the live UDP sink. These are invalid/insufficient oracles, not evidence
that the proxy leaked or that WebTransport was covered.

Linux early oracle: **357 passed, 3 failed, 29 skipped, 2 xpassed** in189.28s.
Besides the same WebTransport fixture gap, both Node-death cases failed before
kill because the command-title substring did not identify the driver. Mac-only
headful cases were explicitly skipped. Controller-death guarded cases passed.

Next fixture corrections use the pinned driver's actual subprocess PID plus
independent parent/child process inventory, and a real trustworthy HTTP loopback
source for WebTransport's positive control. The guarded attempt must additionally
show a fixed-proxy refusal via synthetic-only DevTools diagnostics. No browser
permission, certificate warning, Local Network Access or OS security bypass is
used. The real public preflight is moved before the independent synthetic suite
so an invalid exotic-transport fixture cannot hide that separate read-only result.

Live filling and final sending remain disabled. These receipts are scoped
engineering observations, not installation acceptance on the user's computer.
