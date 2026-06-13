#!/usr/bin/env python3
"""Merge Wells Fargo checking exports and refresh lookup/processed files."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from split_checking_descriptions import load_venmo_entries, rebuild_merchant_lookup, split_checking_csv


DEFAULT_IMPORT_DIR = Path("data/raw/imports")
DEFAULT_CANONICAL_RAW = Path("data/raw/checking_all.csv")
DEFAULT_CANONICAL_VENMO = Path("data/raw/venmo_all.csv")
DEFAULT_LOOKUP = Path("data/lookups/merchant_lookup.csv")
DEFAULT_PROCESSED = Path("data/processed/checking_split.csv")
RAW_FIELDS = ["DATE", "DESCRIPTION", "AMOUNT", "CHECK #", "STATUS"]
VENMO_FIELDS = [
    "ID",
    "Datetime",
    "Type",
    "Status",
    "Note",
    "From",
    "To",
    "Amount (total)",
    "Funding Source",
    "Destination",
]


def parse_date_for_sort(row: dict[str, str]) -> datetime:
    try:
        return datetime.strptime(row["DATE"], "%m/%d/%Y")
    except ValueError:
        return datetime.min


def read_raw_export(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        missing_fields = set(RAW_FIELDS) - set(reader.fieldnames or [])
        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(f"{path} is missing required field(s): {missing}")
        return [{field: row.get(field, "") for field in RAW_FIELDS} for row in reader]


def find_import_files(import_dir: Path, canonical_raw: Path) -> list[Path]:
    if not import_dir.exists():
        return []
    return sorted(
        path
        for path in import_dir.glob("*.csv")
        if path.name.startswith("Wells_Fargo_checking_")
        if path.resolve() != canonical_raw.resolve()
    )


def merge_raw_exports(import_dir: Path, canonical_raw: Path) -> tuple[int, int, int]:
    rows = []
    input_paths = find_import_files(import_dir, canonical_raw)

    for path in input_paths:
        rows.extend(read_raw_export(path))

    rows_by_key = {}
    for row in rows:
        key = tuple(row[field] for field in RAW_FIELDS)
        rows_by_key[key] = row

    merged_rows = sorted(
        rows_by_key.values(),
        key=lambda row: (parse_date_for_sort(row), row["DESCRIPTION"], row["AMOUNT"]),
        reverse=True,
    )

    canonical_raw.parent.mkdir(parents=True, exist_ok=True)
    with canonical_raw.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=RAW_FIELDS)
        writer.writeheader()
        writer.writerows(merged_rows)

    return len(input_paths), len(rows), len(merged_rows)


def write_canonical_venmo(entries: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows_by_id = {
        entry["ID"]: {field: entry.get(field, "") for field in VENMO_FIELDS}
        for entry in entries
        if entry.get("ID")
    }
    output_rows = sorted(
        rows_by_id.values(),
        key=lambda row: row.get("Datetime", ""),
        reverse=True,
    )

    with output_path.open("w", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=VENMO_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge raw Wells Fargo checking exports and refresh processed files."
    )
    parser.add_argument("--import-dir", type=Path, default=DEFAULT_IMPORT_DIR)
    parser.add_argument("--canonical-raw", type=Path, default=DEFAULT_CANONICAL_RAW)
    parser.add_argument("--canonical-venmo", type=Path, default=DEFAULT_CANONICAL_VENMO)
    parser.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP)
    parser.add_argument("--processed", type=Path, default=DEFAULT_PROCESSED)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    file_count, raw_row_count, merged_row_count = merge_raw_exports(
        args.import_dir, args.canonical_raw
    )
    venmo_entries = load_venmo_entries(args.import_dir)
    write_canonical_venmo(venmo_entries, args.canonical_venmo)
    lookup_row_count, filled_lookup_row_count = rebuild_merchant_lookup(
        args.canonical_raw, args.lookup, venmo_entries
    )
    split_checking_csv(
        args.canonical_raw,
        args.processed,
        args.lookup,
        keep_description=False,
        venmo_entries=venmo_entries,
    )

    print(f"Merged {file_count} raw file(s)")
    print(f"Read {raw_row_count} raw row(s)")
    print(f"Wrote {merged_row_count} deduplicated row(s) to {args.canonical_raw}")
    print(
        f"Updated {args.lookup} with {lookup_row_count} lookup row(s); "
        f"{filled_lookup_row_count} already labeled"
    )
    print(f"Read {len(venmo_entries)} Venmo row(s)")
    print(f"Wrote merged Venmo rows to {args.canonical_venmo}")
    print(f"Wrote processed CSV to {args.processed}")


if __name__ == "__main__":
    main()
