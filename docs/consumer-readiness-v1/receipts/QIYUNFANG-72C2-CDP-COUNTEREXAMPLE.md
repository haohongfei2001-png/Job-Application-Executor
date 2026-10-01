# Fresh-CDP lifecycle counterexample

Exact head: `72c22922cb8d59df59e015f504583d6f7675829f`.
Run: https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36848193941
Mac job: `110323302847`.

The Mac early oracle completed **317 passed, 1 failed**. All three new durable
approval/browser integration cases passed. Linux's early oracle passed. The
failed case was the original `pending-cdp_fresh` SIGKILL oracle: an independent
loopback recipient observed `POST /submit-fetch` with the synthetic canary after
the Python controller died. Thus `disposeOnDetach` can release a pending request
before destroying the context. The earlier all-green6e95 run does not establish
this backend as safe. No real applicant data or real submission endpoint was used.

Raw CDP adoption is rejected by the new preparation session owner before browser
or field capability. The original characterization fixture is retained; only its
specific independently observed escape exception is expected, never setup,
milestone, server or cleanup failures. Raw launched mode remains an ordinary
comparison, not a production certification. XFAIL/XPASS comparator outcomes must
be reported separately from guarded-backend passes.

The candidate replacement uses a new disposable browser with a sole deny-only
loopback HTTP proxy, implicit loopback bypass subtracted, QUIC disabled and
non-proxied WebRTC UDP disabled. The listener never reads, logs or forwards a
request. Public allowlisted resources use a separate local API client and local
fulfillment. The proxy never gains a forwarding capability. This is an HTTP-family
boundary, not an OS-wide socket sandbox. Native WebSocket/worker, missing-proxy,
controller-death and Node-driver-death oracles must pass on the new exact head.
Live admission and final transmission remain disabled.

A read-only public preflight diagnostic opens only the public listing/empty popup
and reports exact public resource hashes or value-free rejected URL descriptors.
It never loads applicant profiles, enters fields, uploads, accepts terms, solves
CAPTCHA or submits. Its result is separate from synthetic safety verification.
