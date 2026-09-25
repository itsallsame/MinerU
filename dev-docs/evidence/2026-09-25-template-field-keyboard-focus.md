# Template field keyboard focus

The editable template form allowed keyboard activation of **添加字段** and **移除**, but adding a row left focus on the Add button and removing the focused row dropped focus to the document body. The latter was reproduced by a Chromium regression before the fix.

The form now focuses the new field-code input after adding a row. Removing a row focuses the next field-code input, then the previous one if no next row exists, or the Add button if the form has no rows. The browser regression checks these paths, including visible focus after deleting the sole row.

Mac verification: `npm test` passed 13 tests; `npm run build` generated eight offline assets; `python3 business-web/scripts/run_browser_tests.py` passed all 26 Chromium scenarios. Full keyboard traversal, an actual screen reader, and the Kylin target browser remain unverified, so FE-046 remains open.
