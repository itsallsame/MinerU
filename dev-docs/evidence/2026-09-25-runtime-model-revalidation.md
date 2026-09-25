# Runtime model revalidation after artifact import

The imported-release verifier checks model files before deployment, and the worker checks them at startup. The runtime acceptance script previously trusted that earlier artifact report; a host-side model file changed afterward could still leave the runtime report marked passed. A new focused test failed before the fix because no runtime model-byte recheck existed.

`scripts.verify_business_runtime` now checks the **currently selected** model manifest against the release SHA-256, rehashes every file in the host model directory, and compares the verified file count with both the release record and the prior artifact report. This runs before Docker probing and before a success report is written. The runtime report records `model_files_reverified`, not model bytes or host paths. A changed file, manifest or file count fails closed. This adds a complete model-directory disk read to the acceptance window.

Mac verification: 42 focused runtime tests passed, including a CLI test proving tampered weights fail before Docker commands and no success report is written; full business suite passed 390/2 skipped. This does not prove physical isolation, prevent later host-side tampering, load weights into vLLM or establish Kylin/NVIDIA compatibility. Target runtime and real-document gates remain open.
