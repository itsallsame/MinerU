# Template draft preservation across stale write refreshes

The template manager previously re-rendered the editor when an older save finished and its list refresh returned. If an operator had already opened **新增自定义模板** and typed another draft, that refresh erased the draft. The stale save's final render could also replace the newer view.

The browser regression delays a custom-template PUT, starts a second template draft, and then releases the old PUT. It first failed because the second template code became empty. The editor now snapshots live draft values before template-list refreshes, including field order, type and required flags, restores the focused control, and skips the stale write's final render after a view switch. A stale success message remains suppressed.

Verification on Mac: `npm test` passed 13 unit tests; `npm run build` built eight offline assets; `python3 business-web/scripts/run_browser_tests.py` passed all 26 Chromium regressions, including the delayed-write draft case. The target Kylin browser, real annotated documents, and physically isolated NVIDIA deployment have not been tested.
