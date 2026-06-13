# Finance Dashboard Data Workflow

This repo turns Wells Fargo checking CSV exports into a cleaner transaction CSV for Excel.

## Main Files

- `data/raw/imports/`: drop new Wells Fargo checking CSV exports here.
- `data/raw/checking_all.csv`: canonical merged raw checking export.
- `data/raw/venmo_all.csv`: canonical merged Venmo statement rows.
- `data/lookups/merchant_lookup.csv`: editable merchant/category lookup table.
- `data/processed/checking_split.csv`: cleaned output for Excel.

The raw bank exports and processed CSV files are ignored by git.

## Normal Update Flow

1. Download a new Wells Fargo checking CSV export.
2. Optional: download Venmo statement CSVs for any periods where Venmo detail matters.
3. Put the Wells Fargo and Venmo CSVs in `data/raw/imports/`.
4. Run:

```bash
python3 scripts/update_checking_data.py
```

That script will:

- read every `*.csv` in `data/raw/imports/`
- use only `Wells_Fargo_checking_*.csv` files as bank imports
- read `VenmoStatement_*.csv` files as Venmo detail imports
- merge them into `data/raw/checking_all.csv`
- merge Venmo rows into `data/raw/venmo_all.csv`
- deduplicate transactions using `DATE`, `DESCRIPTION`, `AMOUNT`, `CHECK #`, and `STATUS`
- sort transactions newest first
- enrich matched Wells Fargo `VENMO` rows with Venmo notes/counterparties
- rebuild `data/lookups/merchant_lookup.csv`
- preserve existing `MERCHANT_NORMALIZED`, `CATEGORY`, and `SUBCATEGORY` values
- add blank lookup rows for new `MATCH_TEXT` values
- regenerate `data/processed/checking_split.csv`

## After New Merchants Appear

Open `data/lookups/merchant_lookup.csv` and fill any blank rows:

```csv
MATCH_TEXT,MERCHANT_NORMALIZED,CATEGORY,SUBCATEGORY
OBSIDIAN,Obsidian,Subscriptions,Software
```

Then rerun:

```bash
python3 scripts/update_checking_data.py
```

The processed CSV will pick up the new labels.

## Processed CSV Columns

- `DATE`: Wells Fargo posted date, normalized to `YYYY-MM-DD` in the processed CSV.
- `MONTH`: `YYYY-MM` version of `DATE`, useful for Excel pivots.
- `YEAR`: four-digit year from `DATE`, useful for filtering.
- `DIRECTION`: `deposit` or `withdrawal`, based on `AMOUNT`.
- `SPEND_AMOUNT`: positive amount for withdrawals; blank for deposits.
- `AMOUNT`: original signed transaction amount.
- `IS_TRANSFER`: `true` for internal transfer rows, otherwise `false`.
- `DESCRIPTION_CATEGORY`: parser family, such as `card`, `payment`, `transfer`, `fee`.
- `TRANSACTION_TYPE`: bank transaction type, such as `PURCHASE` or `RECURRING PAYMENT`.
- `MERCHANT_EXTRACTED`: merchant text extracted from the raw bank description.
- `MATCH_TEXT`: mechanically cleaned lookup key.
- `MERCHANT_NORMALIZED`: human-friendly merchant name from the lookup table.
- `CATEGORY`: broad category from the lookup table.
- `SUBCATEGORY`: optional detailed category from the lookup table.
- `LOCATION`, `REGION`, `COUNTRY`: location fields parsed from card transactions.
- `CASH_BACK_AMOUNT`: cash-back amount when Wells Fargo includes it.
- `ACTION`: action for payment-style rows, such as Venmo `PAYMENT` or `CASHOUT`.

## Match Text Rules

`MATCH_TEXT` is intentionally mechanical. It reduces duplicate lookup work without making category decisions.

Current cleanup examples:

- `TST*PAYDIRT` -> `PAYDIRT`
- `SQ *CREMA COFFEE +` -> `CREMA COFFEE`
- `PAR*BURGERVILLE 46` -> `BURGERVILLE`
- `HOP*0383JBJ` -> `TRIMET`
- `HOP*034LTVQ TRIMET` -> `TRIMET`
- `DROPBOX*7BQR2LWZZN` -> `DROPBOX`
- `Amazon Prime*5N6MA` -> `Amazon Prime`
- `FRED-MEYER #0600 3030 NE` -> `FRED-MEYER`
- `MCDONALD'S F7789` -> `MCDONALD'S`
- `STARBUCKS STORE 00` -> `STARBUCKS`

Venmo rows are enriched when matching Venmo statements are present:

- bank row `VENMO PAYMENT` + Venmo note `april groceries` -> `VENMO PAYMENT CHRISTINA GODINEZ APRIL GROCERIES`
- bank row `VENMO CASHOUT` + Venmo standard transfer -> `VENMO CASHOUT`

Human decisions like `OPENAI *CHATGPT SU` -> `ChatGPT` belong in `merchant_lookup.csv`, not parser logic.

## Regenerate Processed CSV Only

If you only edited `merchant_lookup.csv` and do not need to merge raw exports, either run the full updater:

```bash
python3 scripts/update_checking_data.py
```

or run the splitter directly:

```bash
python3 scripts/split_checking_descriptions.py
```

The direct splitter reads:

- `data/raw/checking_all.csv`
- `data/lookups/merchant_lookup.csv`

and writes:

- `data/processed/checking_split.csv`

For the canonical merged raw file, prefer `scripts/update_checking_data.py`.
