# Full closure failure: acknowledgement observer scheduling

- Exact head: `1b1e0d4e897f115e85732b7771de4f4cd22fb127`
- All eight targeted jobs passed in [run 36790575648](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36790575648)
- Full closure [run 36792907341](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36792907341)
  failed: **1 failed, 2242 passed, 16 skipped, 132 subtests passed**, in 2064.26s
  (34m24s). The unsigned build correctly did not run.

The failure was the retained
`test_http_service_retirement_sends_ack_before_worker_stops` immediate
`response_written.is_set()` assertion. The real client had already received the
exact successful stop acknowledgement. The test marker is set only after the
server's response writer returns; reading a Content-Length-delimited body does
not wait for that server-side observer or the subsequent stop flag.

A separate bounded event-gated probe deterministically reproduced both windows:
client return after response bytes but before the observer marker, and client
return after the marker but before the actual stop flag. Releasing the gate and
waiting for completion preserved ACK-before-stop and the complete original
private-state/key assertions. This is evidence about the test's observation
ordering; it does not justify changing production shutdown behavior.

The correction adds one bounded wait on the existing worker stop event before
all original assertions. It retains the original guard that rejects stopping
before the response marker. Three additive HTTP cases pause before writing,
after writing and before stopping; the before-write case also proves the
premature-stop guard rejects a negative order. All use the complete existing
retirement fixture, explicit events rather than sleeps, and unchanged full
private-state/key checks. Production response/flush/stop code, test selection,
platforms, CI timeouts and final-submit authority are unchanged.

The corrected head requires its own targeted and full closure evidence. This
failed run is not a PASS, and a future artifact must identify that newly tested
head rather than this one. No healthy-job retry or deadline/budget increase was
used to hide the failure.
