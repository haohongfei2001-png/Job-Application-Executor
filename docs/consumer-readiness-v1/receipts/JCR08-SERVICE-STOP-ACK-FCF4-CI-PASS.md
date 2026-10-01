# JCR-08 service stop acknowledgement engineering receipt

- Repository: `haohongfei2001-png/Job-Application-Executor`
- Sole writer: Draft [PR #20](https://github.com/haohongfei2001-png/Job-Application-Executor/pull/20), branch `feat/jcr08-consumer-app`
- Base main at verification: `753688f0a5cb8b69463ff747b47e32c86335c969`
- Exact tested code head: `fcf420d398e651d3eee52d0a67e8e644eb486967`
- [Application Executor CI 36713923318](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36713923318): **completed success** for this exact head.

## Result

The authenticated exact-instance service stop now fences mutations before acknowledging retirement and releases the worker stop event after the HTTP response write and flush attempt. The existing CLI still validates the owned service identity and waits for the owned registry to disappear. A new complete test asserts response-before-worker-stop ordering and private synthetic authority preservation; the existing actual hosted Mac native integration and packaged consumer tests remain selected.

The preceding `70d59f082db9feb1411c20bb6eaf0e7cd9453025` run [36707007883](https://github.com/haohongfei2001-png/Job-Application-Executor/actions/runs/36707007883) failed at test collection because the `replace_registry` parametrization belonged to the following existing isolated-service test. Commit `fcf420d398e651d3eee52d0a67e8e644eb486967` restored that decorator to its owning test without removing either test or reducing its cases. The new exact-head run passed foundation, all three packaged candidate shards and all four hosted Mac candidate shards. The non-Draft full `test` job remained skipped under the existing Draft PR selection; this receipt does not claim that full gate.

## Limits and continuation

This is a bounded JCR-08 engineering pass, not consumer certification. JCR-08 remains IN_PROGRESS / NOT_CERTIFIED and formal JCR-09 remains NOT_STARTED in canonical STATUS. JCR-09 frozen diverse 100-task acceptance, complete platform/fault repetitions, 24-hour soak, public/live acceptance and final owner/device/private/signing/security evidence remain open or deferred by their own dependencies. No real application was submitted; final submit remains human-only. The documentation commit after this tested head is not the tested code SHA.
