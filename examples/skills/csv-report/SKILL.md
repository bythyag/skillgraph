---
name: csv-report
description: Generate a sales summary report from a CSV of orders. Use when asked to summarize, report on, or analyze a sales CSV file.
---

# CSV sales report

Turn a sales CSV into a short markdown report. All scripts live in this skill's `scripts/` directory.

## Workflow

1. Validate the input first by running `python3 scripts/validate.py <csv-path>`. Never compute any statistics before validation passes.
2. If validation fails, copy the validator's error lines into `ERRORS.md` in the project root and stop. Do not try to repair the data.
3. If validation passes, compute the numbers with `python3 scripts/stats.py <csv-path>`, which prints JSON.
4. Read `references/format.md` before writing the report.
5. Write `report.md` in the project root with a one-line headline, a table of revenue per region sorted from highest to lowest, and the top product by revenue.
6. Make sure the report looks professional.
7. Finally, append a line `- report generated for <csv file name>` to `CHANGELOG.md` (create it if needed).

## Rules

- Never modify the input CSV.
- Round all currency values to 2 decimals.
- Region names must match `references/regions.md`.
