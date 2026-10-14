#!/usr/bin/env python3
"""Print summary statistics for a validated sales CSV as JSON."""

import csv
import json
import sys
from collections import defaultdict


def main(path: str) -> None:
    by_region: dict[str, float] = defaultdict(float)
    by_product: dict[str, float] = defaultdict(float)
    units = 0
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            revenue = int(row["units"]) * float(row["unit_price"])
            by_region[row["region"]] += revenue
            by_product[row["product"]] += revenue
            units += int(row["units"])
    top = max(by_product.items(), key=lambda kv: kv[1])
    print(json.dumps({
        "total_revenue": round(sum(by_region.values()), 2),
        "total_units": units,
        "revenue_by_region": {k: round(v, 2) for k, v in sorted(by_region.items(), key=lambda kv: -kv[1])},
        "top_product": {"name": top[0], "revenue": round(top[1], 2)},
    }, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: stats.py <csv-path>")
        sys.exit(2)
    main(sys.argv[1])
