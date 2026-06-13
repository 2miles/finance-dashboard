#!/usr/bin/env python3
"""Split Wells Fargo checking transaction descriptions into structured fields."""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


DEFAULT_INPUT = Path("data/raw/checking_all.csv")
DEFAULT_OUTPUT = Path("data/processed/checking_split.csv")
DEFAULT_LOOKUP = Path("data/lookups/merchant_lookup.csv")
DEFAULT_VENMO_DIR = Path("data/raw/imports")
LOOKUP_FIELDS = ["MATCH_TEXT", "MERCHANT_NORMALIZED", "CATEGORY", "SUBCATEGORY"]

NEW_FIELDS = [
    "MONTH",
    "YEAR",
    "DIRECTION",
    "SPEND_AMOUNT",
    "IS_TRANSFER",
    "DESCRIPTION_CATEGORY",
    "TRANSACTION_TYPE",
    "MERCHANT_EXTRACTED",
    "MATCH_TEXT",
    "MERCHANT_NORMALIZED",
    "CATEGORY",
    "SUBCATEGORY",
    "LOCATION",
    "REGION",
    "COUNTRY",
    "CASH_BACK_AMOUNT",
    "ACTION",
]

CARD_REFERENCE_RE = re.compile(r"\s+(?P<reference>[A-Z]\d{12,})\s+CARD\s+(?P<card>\d{4})\s*$")
ATM_REFERENCE_RE = re.compile(
    r"\s+(?P<reference>\d{12,})\s+ATM ID\s+(?P<atm_id>\S+)\s+CARD\s+(?P<card>\d{4})\s*$"
)
CARD_AUTH_RE = re.compile(
    r"^(?P<type>.+?)\s+(?:\$\s*(?P<cash_back>[0-9.,]+)\s+)?"
    r"AUTHORIZED ON\s+(?P<authorized_on>\d{2}/\d{2})\s+(?P<tail>.+)$"
)
TRANSFER_RE = re.compile(
    r"^(?P<type>ONLINE TRANSFER FROM|RECURRING TRANSFER TO)\s+"
    r"(?P<account>.+?)\s+REF\s+#(?P<reference>\S+)"
    r"(?:\s+(?P<masked_account>X+\d+))?(?:\s+ON\s+(?P<payment_date>\d{2}/\d{2}/\d{2}))?$"
)
INSTANT_PAYMENT_RE = re.compile(
    r"^(?P<type>Instant Pmt from)\s+(?P<counterparty>.+?)\s+on\s+"
    r"(?P<payment_date>\d{2}/\d{2})\s+Ref#(?P<reference>\S+)$"
)
EDEPOSIT_RE = re.compile(
    r"^(?P<type>eDeposit in Branch)\s+(?P<payment_date>\d{2}/\d{2}/\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2}\s+[AP]M)\s+(?P<location>.+?)\s+(?P<region>[A-Z]{2})\s+"
    r"(?P<account>\d{4})$"
)
ZELLE_RE = re.compile(r"^ZELLE\s+(?:FROM|TO)\s+.+$")
ZELLE_PARTY_RE = re.compile(r"^(ZELLE\s+(?P<direction>FROM|TO)\s+(?P<first_name>\S+)).*$")
MATCH_PROCESSOR_PREFIXES = ("TST*", "SQ *", "PAR*", "HOP*")


def blank_fields() -> dict[str, str]:
    return {field: "" for field in NEW_FIELDS}


def split_double_spaced(description: str) -> list[str]:
    return [part.strip() for part in re.split(r"\s{2,}", description.strip()) if part.strip()]


def split_location(parts: list[str]) -> tuple[str, str, str]:
    if not parts:
        return "", "", ""

    location = " ".join(parts).strip()
    region = ""
    country = ""

    last_part = parts[-1]
    if re.fullmatch(r"[A-Z]{2}", last_part):
        region = last_part
        location = " ".join(parts[:-1]).strip()
    elif re.fullmatch(r"[A-Z]{3}", last_part):
        country = last_part
        location = " ".join(parts[:-1]).strip()
    else:
        match = re.match(r"^(?P<location>.+?)\s+(?P<region>[A-Z]{2})$", last_part)
        if match:
            region = match.group("region")
            location = " ".join(parts[:-1] + [match.group("location")]).strip()

    return location, region, country


def create_match_text(parsed: dict[str, str]) -> str:
    if parsed.get("MERCHANT_EXTRACTED", "").startswith("Instant Pmt from VENMO"):
        return parsed["MERCHANT_EXTRACTED"]
    if parsed.get("DESCRIPTION_CATEGORY") == "venmo" and parsed.get("ACTION") == "CASHOUT":
        return "VENMO CASHOUT"
    if parsed.get("DESCRIPTION_CATEGORY") == "venmo" and parsed.get("MERCHANT_EXTRACTED"):
        return parsed["MERCHANT_EXTRACTED"]

    if parsed.get("DESCRIPTION_CATEGORY") == "payment" and parsed.get("ACTION"):
        return " ".join([parsed.get("TRANSACTION_TYPE", ""), parsed.get("ACTION", "")]).strip()

    match_text = parsed.get("MERCHANT_EXTRACTED") or parsed.get("TRANSACTION_TYPE", "")
    match_text = " ".join(match_text.split()).strip()
    if not match_text:
        return ""
    if match_text.startswith("HOP*"):
        return "TRIMET"
    zelle_match = re.match(
        r"^(ZELLE\s+(?:FROM|TO)\s+.+?)\s+ON\s+\d{2}/\d{2}\s+REF\s+#\s+\S+\s*(?P<memo>.*)$",
        match_text,
    )
    if zelle_match:
        memo = zelle_match.group("memo").strip()
        if re.search(r"\bRENT\b", memo, flags=re.IGNORECASE):
            memo = "RENT"
        return " ".join(part for part in [zelle_match.group(1), memo] if part)

    for prefix in MATCH_PROCESSOR_PREFIXES:
        if match_text.startswith(prefix):
            match_text = match_text.removeprefix(prefix).strip()
            break

    match_text = re.sub(r"^Amazon Prime\*[^ ]+$", "Amazon Prime", match_text, flags=re.IGNORECASE)
    match_text = re.sub(r"^DROPBOX\*[^ ]+$", "DROPBOX", match_text)
    match_text = re.sub(r"\s+#\s*\d+(?:\s+[0-9A-Z]{1,5})*$", "", match_text)
    match_text = re.sub(r"\s+\d{2,}[A-Z]?$", "", match_text)
    match_text = re.sub(r"\s+F\d{3,}$", "", match_text)
    match_text = re.sub(r"\s+[&+-]+$", "", match_text)
    match_text = re.sub(r"\s+", " ", match_text).strip()

    if match_text == "STARBUCKS STORE":
        match_text = "STARBUCKS"

    return match_text


def extract_card_merchant(description: str) -> str:
    reference_match = CARD_REFERENCE_RE.search(description)
    if not reference_match:
        return ""

    before_reference = description[: reference_match.start()].strip()
    auth_match = CARD_AUTH_RE.match(before_reference)
    if not auth_match:
        return ""

    parts = split_double_spaced(auth_match.group("tail"))
    return parts[0] if parts else ""


def parse_card_description(description: str) -> dict[str, str] | None:
    reference_match = CARD_REFERENCE_RE.search(description)
    if not reference_match:
        return None

    before_reference = description[: reference_match.start()].strip()
    auth_match = CARD_AUTH_RE.match(before_reference)
    if not auth_match:
        return None

    parts = split_double_spaced(auth_match.group("tail"))
    merchant = extract_card_merchant(description)
    location, region, country = split_location(parts[1:])

    parsed = blank_fields()
    parsed.update(
        {
            "DESCRIPTION_CATEGORY": "card",
            "TRANSACTION_TYPE": " ".join(auth_match.group("type").split()),
            "MERCHANT_EXTRACTED": merchant,
            "LOCATION": location,
            "REGION": region,
            "COUNTRY": country,
            "CASH_BACK_AMOUNT": auth_match.group("cash_back") or "",
        }
    )
    return parsed


def parse_atm_description(description: str) -> dict[str, str] | None:
    reference_match = ATM_REFERENCE_RE.search(description)
    if not reference_match:
        return None

    before_reference = description[: reference_match.start()].strip()
    auth_match = CARD_AUTH_RE.match(before_reference)
    if not auth_match:
        return None

    parts = split_double_spaced(auth_match.group("tail"))
    location, region, country = split_location(parts)

    parsed = blank_fields()
    parsed.update(
        {
            "DESCRIPTION_CATEGORY": "atm",
            "TRANSACTION_TYPE": " ".join(auth_match.group("type").split()),
            "LOCATION": location,
            "REGION": region,
            "COUNTRY": country,
        }
    )
    return parsed


def parse_fee_description(description: str) -> dict[str, str] | None:
    fee_types = {
        "INTERNATIONAL PURCHASE TRANSACTION FEE",
        "NON-WELLS FARGO ATM TRANSACTION FEE",
    }
    normalized = " ".join(description.split())
    if normalized not in fee_types:
        return None

    parsed = blank_fields()
    parsed.update(
        {
            "DESCRIPTION_CATEGORY": "fee",
            "TRANSACTION_TYPE": normalized,
        }
    )
    return parsed


def parse_transfer_description(description: str) -> dict[str, str] | None:
    match = TRANSFER_RE.match(description)
    if not match:
        return None

    parsed = blank_fields()
    parsed.update(
        {
            "DESCRIPTION_CATEGORY": "transfer",
            "TRANSACTION_TYPE": match.group("type"),
        }
    )
    return parsed


def parse_instant_payment_description(description: str) -> dict[str, str] | None:
    match = INSTANT_PAYMENT_RE.match(description)
    if not match:
        return None

    counterparty = match.group("counterparty").strip()
    if counterparty == "VENMO":
        payment_date = match.group("payment_date")
        parsed = blank_fields()
        parsed.update(
            {
                "DESCRIPTION_CATEGORY": "venmo",
                "TRANSACTION_TYPE": "VENMO CASHOUT",
                "MERCHANT_EXTRACTED": f"Instant Pmt from VENMO on {payment_date}",
                "ACTION": "CASHOUT",
            }
        )
        return parsed

    parsed = blank_fields()
    parsed.update(
        {
            "DESCRIPTION_CATEGORY": "instant_payment",
            "TRANSACTION_TYPE": match.group("type"),
        }
    )
    return parsed


def parse_edeposit_description(description: str) -> dict[str, str] | None:
    match = EDEPOSIT_RE.match(description)
    if not match:
        return None

    parsed = blank_fields()
    parsed.update(
        {
            "DESCRIPTION_CATEGORY": "deposit",
            "TRANSACTION_TYPE": match.group("type"),
            "LOCATION": match.group("location"),
            "REGION": match.group("region"),
        }
    )
    return parsed


def parse_zelle_description(description: str) -> dict[str, str] | None:
    normalized = " ".join(description.split())
    if not ZELLE_RE.match(normalized):
        return None

    party_match = ZELLE_PARTY_RE.match(normalized)
    transaction_type = (
        f"ZELLE {party_match.group('direction')} {party_match.group('first_name')}"
        if party_match
        else normalized
    )

    parsed = blank_fields()
    parsed.update(
        {
            "DESCRIPTION_CATEGORY": "zelle",
            "TRANSACTION_TYPE": transaction_type,
            "MERCHANT_EXTRACTED": normalized,
        }
    )
    return parsed


def parse_double_spaced_payment(description: str) -> dict[str, str] | None:
    parts = split_double_spaced(description)
    if len(parts) < 3:
        return None

    transaction_type, action, date_reference, *counterparty_parts = parts
    match = re.match(
        r"^(?P<payment_date>\d{6})(?:\s+(?P<reference>\S+))?(?:\s+(?P<counterparty>.+))?$",
        date_reference,
    )
    if not match:
        return None

    parsed = blank_fields()
    parsed.update(
        {
            "DESCRIPTION_CATEGORY": "payment",
            "TRANSACTION_TYPE": transaction_type,
            "ACTION": action,
        }
    )

    return parsed


def parse_fallback_description(description: str) -> dict[str, str]:
    parts = split_double_spaced(description)
    parsed = blank_fields()
    parsed["DESCRIPTION_CATEGORY"] = "uncategorized"
    parsed["TRANSACTION_TYPE"] = parts[0] if parts else description.strip()
    return parsed


def parse_description(description: str) -> dict[str, str]:
    parsers = (
        parse_card_description,
        parse_atm_description,
        parse_fee_description,
        parse_transfer_description,
        parse_instant_payment_description,
        parse_edeposit_description,
        parse_zelle_description,
        parse_double_spaced_payment,
    )
    for parser in parsers:
        parsed = parser(description)
        if parsed is not None:
            return parsed
    return parse_fallback_description(description)


def parse_direction(amount: str) -> str:
    try:
        parsed_amount = float((amount or "").replace(",", ""))
    except ValueError:
        return ""

    if parsed_amount > 0:
        return "deposit"
    if parsed_amount < 0:
        return "withdrawal"
    return ""


def parse_bank_date(date: str) -> datetime | None:
    try:
        return datetime.strptime(date, "%m/%d/%Y")
    except ValueError:
        return None


def parse_money(amount: str) -> Decimal | None:
    cleaned = (amount or "").replace("$", "").replace(",", "").replace(" ", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_month(date: str) -> str:
    match = re.fullmatch(r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})", (date or "").strip())
    if not match:
        return ""
    return f"{match.group('year')}-{int(match.group('month')):02d}"


def parse_iso_date(date: str) -> str:
    match = re.fullmatch(r"(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})", (date or "").strip())
    if not match:
        return date or ""
    return f"{match.group('year')}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"


def parse_year(date: str) -> str:
    match = re.fullmatch(r"\d{1,2}/\d{1,2}/(?P<year>\d{4})", (date or "").strip())
    return match.group("year") if match else ""


def parse_spend_amount(amount: str) -> str:
    try:
        parsed_amount = float((amount or "").replace(",", ""))
    except ValueError:
        return ""

    if parsed_amount < 0:
        return f"{abs(parsed_amount):.2f}"
    return ""


def parse_is_transfer(parsed: dict[str, str]) -> str:
    if parsed.get("DESCRIPTION_CATEGORY") == "transfer":
        return "true"
    if parsed.get("CATEGORY", "").casefold() == "transfers":
        return "true"
    return "false"


def load_merchant_lookup(lookup_path: Path) -> list[dict[str, str]]:
    if not lookup_path.exists():
        return []

    with lookup_path.open(newline="") as lookup_file:
        reader = csv.DictReader(lookup_file)
        required_fields = set(LOOKUP_FIELDS)
        missing_fields = required_fields - set(reader.fieldnames or [])
        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(f"{lookup_path} is missing required field(s): {missing}")
        return [row for row in reader if row.get("MATCH_TEXT", "").strip()]


def clean_lookup_value(row: dict[str, str], field: str) -> str:
    return (row.get(field) or "").strip()


def apply_merchant_lookup(
    parsed: dict[str, str], description: str, lookup_rows: list[dict[str, str]]
) -> None:
    lookup_text = parsed.get("MATCH_TEXT", "").casefold()
    for lookup_row in lookup_rows:
        match_text = lookup_row["MATCH_TEXT"].strip().casefold()
        if match_text == lookup_text:
            parsed["MERCHANT_NORMALIZED"] = clean_lookup_value(lookup_row, "MERCHANT_NORMALIZED")
            parsed["CATEGORY"] = clean_lookup_value(lookup_row, "CATEGORY")
            parsed["SUBCATEGORY"] = clean_lookup_value(lookup_row, "SUBCATEGORY")
            return


def read_venmo_statement(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as venmo_file:
        rows = list(csv.reader(venmo_file))

    header_index = None
    for index, row in enumerate(rows):
        if "ID" in row and "Datetime" in row and "Amount (total)" in row:
            header_index = index
            break

    if header_index is None:
        return []

    headers = rows[header_index]
    entries = []
    for row in rows[header_index + 1 :]:
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        record = dict(zip(headers, row))
        if not record.get("ID") or not record.get("Datetime") or not record.get("Amount (total)"):
            continue
        entries.append(record)
    return entries


def load_venmo_entries(venmo_dir: Path) -> list[dict[str, str]]:
    if not venmo_dir.exists():
        return []

    entries = []
    for path in sorted(venmo_dir.glob("VenmoStatement_*.csv")):
        entries.extend(read_venmo_statement(path))
    return entries


def venmo_entry_date(entry: dict[str, str]) -> datetime | None:
    try:
        return datetime.fromisoformat(entry["Datetime"])
    except ValueError:
        return None


def venmo_entry_amount(entry: dict[str, str]) -> Decimal | None:
    return parse_money(entry.get("Amount (total)", ""))


def venmo_counterparty(entry: dict[str, str]) -> str:
    names = [entry.get("From", "").strip(), entry.get("To", "").strip()]
    for name in names:
        if name and name.casefold() != "miles whitaker":
            return name
    return next((name for name in names if name), "")


def format_venmo_match_text(entry: dict[str, str], action: str) -> str:
    note = " ".join(entry.get("Note", "").split()).strip()
    counterparty = venmo_counterparty(entry)
    entry_type = entry.get("Type", "").strip().upper()

    if action == "CASHOUT" or entry_type == "STANDARD TRANSFER":
        return "VENMO CASHOUT"

    parts = ["VENMO", action or entry_type]
    if counterparty:
        parts.append(counterparty.upper())
    if note:
        parts.append(note.upper())
    return " ".join(parts)


def find_matching_venmo_entry(
    row: dict[str, str], parsed: dict[str, str], venmo_entries: list[dict[str, str]]
) -> dict[str, str] | None:
    if parsed.get("TRANSACTION_TYPE") != "VENMO":
        return None

    bank_date = parse_bank_date(row.get("DATE", ""))
    bank_amount = parse_money(row.get("AMOUNT", ""))
    if bank_date is None or bank_amount is None:
        return None

    action = parsed.get("ACTION", "")
    candidates = []
    for entry in venmo_entries:
        entry_date = venmo_entry_date(entry)
        entry_amount = venmo_entry_amount(entry)
        if entry_date is None or entry_amount is None:
            continue

        day_delta = (bank_date.date() - entry_date.date()).days
        if action == "CASHOUT":
            if entry.get("Type") != "Standard Transfer":
                continue
            expected_bank_amount = -entry_amount
            if expected_bank_amount == bank_amount and 0 <= day_delta <= 5:
                candidates.append((day_delta, entry))
        elif action == "PAYMENT":
            if entry_amount == bank_amount and 0 <= day_delta <= 3:
                candidates.append((day_delta, entry))

    if not candidates:
        return None
    return sorted(candidates, key=lambda candidate: candidate[0])[0][1]


def apply_venmo_enrichment(
    row: dict[str, str], parsed: dict[str, str], venmo_entries: list[dict[str, str]]
) -> None:
    entry = find_matching_venmo_entry(row, parsed, venmo_entries)
    if entry is None:
        return

    action = parsed.get("ACTION", "")
    parsed["DESCRIPTION_CATEGORY"] = "venmo"
    parsed["TRANSACTION_TYPE"] = f"VENMO {action}" if action else "VENMO"
    parsed["MERCHANT_EXTRACTED"] = format_venmo_match_text(entry, action)


def rebuild_merchant_lookup(
    input_path: Path, lookup_path: Path, venmo_entries: list[dict[str, str]] | None = None
) -> tuple[int, int]:
    venmo_entries = venmo_entries or []
    existing_lookup_rows = load_merchant_lookup(lookup_path)
    existing_by_match = {}
    for row in existing_lookup_rows:
        match_text = row["MATCH_TEXT"].strip()
        values = {
            "MERCHANT_NORMALIZED": clean_lookup_value(row, "MERCHANT_NORMALIZED"),
            "CATEGORY": clean_lookup_value(row, "CATEGORY"),
            "SUBCATEGORY": clean_lookup_value(row, "SUBCATEGORY"),
        }
        if any(values.values()) and match_text.casefold() not in existing_by_match:
            existing_by_match[match_text.casefold()] = values

    with input_path.open(newline="") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames:
            raise ValueError(f"{input_path} has no header row")

        rows_by_match = {}
        for row in reader:
            parsed = parse_description(row.get("DESCRIPTION", ""))
            apply_venmo_enrichment(row, parsed, venmo_entries)
            match_text = create_match_text(parsed)
            if not match_text:
                continue

            values = existing_by_match.get(
                match_text.casefold(),
                {"MERCHANT_NORMALIZED": "", "CATEGORY": "", "SUBCATEGORY": ""},
            )
            existing = rows_by_match.get(match_text.casefold())
            if existing is None:
                rows_by_match[match_text.casefold()] = {"MATCH_TEXT": match_text, **values}
            elif not any(existing[field] for field in LOOKUP_FIELDS[1:]) and any(values.values()):
                existing.update(values)

    lookup_path.parent.mkdir(parents=True, exist_ok=True)
    output_rows = sorted(rows_by_match.values(), key=lambda row: row["MATCH_TEXT"].casefold())
    with lookup_path.open("w", newline="") as lookup_file:
        writer = csv.DictWriter(lookup_file, fieldnames=LOOKUP_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    filled_rows = sum(
        1
        for row in output_rows
        if row["MERCHANT_NORMALIZED"] or row["CATEGORY"] or row["SUBCATEGORY"]
    )
    return len(output_rows), filled_rows


def split_checking_csv(
    input_path: Path,
    output_path: Path,
    lookup_path: Path,
    keep_description: bool,
    venmo_entries: list[dict[str, str]] | None = None,
) -> None:
    lookup_rows = load_merchant_lookup(lookup_path)
    venmo_entries = venmo_entries or []

    with input_path.open(newline="") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames:
            raise ValueError(f"{input_path} has no header row")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        excluded_source_fields = {"CHECK #", "STATUS"}
        source_fieldnames = [
            field for field in reader.fieldnames if field not in excluded_source_fields
        ]
        if not keep_description and "DESCRIPTION" in source_fieldnames:
            source_fieldnames.remove("DESCRIPTION")
        fieldnames = []
        for field in source_fieldnames:
            fieldnames.append(field)
            if field == "DATE":
                fieldnames.append("MONTH")
                fieldnames.append("YEAR")
                fieldnames.append("DIRECTION")
                fieldnames.append("SPEND_AMOUNT")
        fieldnames.extend(field for field in NEW_FIELDS if field not in fieldnames)

        with output_path.open("w", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                description = row.get("DESCRIPTION", "")
                parsed = parse_description(description)
                apply_venmo_enrichment(row, parsed, venmo_entries)
                parsed["MONTH"] = parse_month(row.get("DATE", ""))
                parsed["YEAR"] = parse_year(row.get("DATE", ""))
                parsed["DIRECTION"] = parse_direction(row.get("AMOUNT", ""))
                parsed["SPEND_AMOUNT"] = parse_spend_amount(row.get("AMOUNT", ""))
                parsed["MATCH_TEXT"] = create_match_text(parsed)
                apply_merchant_lookup(parsed, description, lookup_rows)
                parsed["IS_TRANSFER"] = parse_is_transfer(parsed)
                output_row = {field: row[field] for field in source_fieldnames}
                if "DATE" in output_row:
                    output_row["DATE"] = parse_iso_date(output_row["DATE"])
                writer.writerow({**output_row, **parsed})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split the DESCRIPTION column in a Wells Fargo Checking.csv export."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help=f"default: {DEFAULT_INPUT}")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"default: {DEFAULT_OUTPUT}")
    parser.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP, help=f"default: {DEFAULT_LOOKUP}")
    parser.add_argument("--venmo-dir", type=Path, default=DEFAULT_VENMO_DIR, help=f"default: {DEFAULT_VENMO_DIR}")
    parser.add_argument(
        "--keep-description",
        action="store_true",
        help="include the raw DESCRIPTION column in the processed output",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    venmo_entries = load_venmo_entries(args.venmo_dir)
    split_checking_csv(args.input, args.output, args.lookup, args.keep_description, venmo_entries)
    print(f"Wrote split checking CSV to {args.output}")


if __name__ == "__main__":
    main()
