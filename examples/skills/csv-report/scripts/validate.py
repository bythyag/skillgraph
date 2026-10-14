#!/usr/bin/env python3
"""Validate a sales CSV. Prints OK, or one ERROR line per problem and exits 1."""

import csv
import sys

REQUIRED = ["date", "region", "product", "units", "unit_price"]


def main(path: str) -> int:
    errors = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            print(f"ERROR header: missing columns {', '.join(missing)}")
            return 1
        for n, row in enumerate(reader, start=2):
            if not row["region"].strip():
                errors.append(f"ERROR line {n}: empty region")
            try:
                if int(row["units"]) < 0:
                    errors.append(f"ERROR line {n}: negative units ({row['units']})")
            except ValueError:
                errors.append(f"ERROR line {n}: units is not an integer ({row['units']!r})")
            try:
                float(row["unit_price"])
            except ValueError:
                errors.append(f"ERROR line {n}: unit_price is not a number ({row['unit_price']!r})")
    for e in errors:
        print(e)
    if not errors:
        print("OK")
    return 1 if errors else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: validate.py <csv-path>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
