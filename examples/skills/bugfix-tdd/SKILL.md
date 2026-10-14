---
name: bugfix-tdd
description: Fix a reported bug in a Python project with a test-first workflow. Use when asked to fix a bug, regression, or wrong output in Python code.
---

# Test-first bug fix

Read `references/conventions.md` before touching any code.

1. Reproduce the bug first: add a test to the `tests/` directory that captures the reported behavior.
2. Run the suite with `python3 -m unittest discover -s tests -t .` and confirm the new test fails.
3. Fix the bug in the source code. Keep the change minimal.
4. Re-run the full suite and confirm every test passes.
5. Write `FIX_NOTES.md` with three headings: Root cause, Fix, Test.

Constraints:
- Never edit or delete existing tests to make the suite pass.
- Do not commit with git; leave committing to the user.
- Handle edge cases appropriately.
