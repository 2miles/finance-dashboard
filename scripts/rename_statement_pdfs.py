#!/usr/bin/env python3
"""Normalize downloaded bank statement PDF filenames."""

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_INPUT_DIR = Path("data/raw/imports/incoming")
DEFAULT_OUTPUT_DIR = Path("data/raw/imports/statements")
DEFAULT_INSTITUTION = "wells-fargo"
DEFAULT_ACCOUNT = "checking"

STATEMENT_RE = re.compile(
    r"^(?P<month>\d{2})(?P<day>\d{2})(?P<year>\d{2})\s+WellsFargo\.pdf$",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class RenamePlan:
    source: Path
    destination: Path


def parse_statement_date(path: Path) -> datetime:
    match = STATEMENT_RE.match(path.name)
    if not match:
        raise ValueError(f"unsupported filename: {path}")

    parsed = datetime.strptime(
        f"{match.group('month')}{match.group('day')}{match.group('year')}",
        "%m%d%y",
    )
    parent_year = path.parent.name
    if parent_year.isdigit() and int(parent_year) != parsed.year:
        raise ValueError(
            f"year folder mismatch for {path}: folder is {parent_year}, "
            f"filename parses as {parsed.year}"
        )
    return parsed


def build_rename_plans(
    input_dir: Path,
    output_dir: Path,
    institution: str,
    account: str,
) -> list[RenamePlan]:
    plans = []
    for source in sorted(input_dir.glob("*/*.pdf")):
        statement_date = parse_statement_date(source)
        filename = (
            f"{statement_date:%Y-%m-%d}_{institution}_{account}.pdf"
        )
        destination = output_dir / f"{statement_date:%Y}" / filename
        plans.append(RenamePlan(source=source, destination=destination))
    return plans


def apply_renames(plans: list[RenamePlan], copy: bool) -> None:
    destinations = [plan.destination for plan in plans]
    duplicates = {path for path in destinations if destinations.count(path) > 1}
    if duplicates:
        duplicate_list = "\n".join(f"  {path}" for path in sorted(duplicates))
        raise ValueError(f"multiple source files would write the same destination:\n{duplicate_list}")

    for plan in plans:
        if plan.destination.exists():
            raise FileExistsError(f"destination already exists: {plan.destination}")

    for plan in plans:
        plan.destination.parent.mkdir(parents=True, exist_ok=True)
        if copy:
            shutil.copy2(plan.source, plan.destination)
        else:
            plan.source.rename(plan.destination)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rename Wells Fargo statement PDFs from MMDDYY WellsFargo.pdf."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--institution", default=DEFAULT_INSTITUTION)
    parser.add_argument("--account", default=DEFAULT_ACCOUNT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the rename. Without this flag, only print the planned changes.",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy files instead of moving them. Requires --apply.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.copy and not args.apply:
        raise SystemExit("--copy requires --apply")

    plans = build_rename_plans(
        args.input_dir,
        args.output_dir,
        args.institution,
        args.account,
    )

    if not plans:
        print(f"No statement PDFs found under {args.input_dir}")
        return

    action = "Copy" if args.copy else "Move"
    print(f"{action if args.apply else 'Would move'} {len(plans)} statement PDF(s):")
    for plan in plans:
        print(f"  {plan.source} -> {plan.destination}")

    if args.apply:
        apply_renames(plans, copy=args.copy)


if __name__ == "__main__":
    main()
