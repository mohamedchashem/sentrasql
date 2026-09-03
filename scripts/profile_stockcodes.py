"""Console-only scan of StockCode-format anomalies in the raw dataset.

Loads the dataset by reusing ``profile_dataset.find_dataset_file`` and
``profile_dataset.load_dataset`` (identical file detection + UTF-8/latin1
encoding fallback to ``profile_anomalies.py``). Reports structural StockCode
anomalies, zero-price descriptions, and Quantity/Invoice sign mismatches.
Investigation only -- no cleaning/transformation, nothing written to disk.
"""

from __future__ import annotations

import re
import sys

import pandas as pd

from profile_dataset import find_dataset_file, load_dataset

# Typical product code: digits optionally followed by one or two letters.
_TYPICAL_STOCKCODE_RE = re.compile(r"^[0-9]+[A-Za-z]{0,2}$")


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value))


def _label(value: object) -> str:
    """Human-readable label for a value, covering missing/empty cases."""
    if _is_missing(value):
        return "(missing/NaN)"
    text = str(value)
    return "(empty string)" if text == "" else text


def _one_line(text: object) -> str:
    """Render a value as a single console line, mapping missing to text."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return "(no description present)"
    return str(text).replace("\r", " ").replace("\n", " ")


def _first_description(df: pd.DataFrame, code: object) -> str:
    """Return the first non-empty Description for a given StockCode value."""
    if _is_missing(code):
        mask = df["StockCode"].isna()
    else:
        mask = df["StockCode"] == code
    for description in df.loc[mask, "Description"].dropna():
        if str(description).strip() != "":
            return _one_line(description)
    return "(no non-empty description present)"


def _is_typical_stockcode(value: object) -> bool:
    if _is_missing(value):
        return False
    return _TYPICAL_STOCKCODE_RE.fullmatch(str(value)) is not None


def _main() -> None:
    dataset_path = find_dataset_file()
    df, _ = load_dataset(dataset_path)

    print(f"Dataset: {dataset_path.name} ({len(df):,} rows)")
    print()

    code_counts = df["StockCode"].value_counts(dropna=False)
    print("=== StockCode values NOT matching 'digits [optional 1-2 letters]' ===")
    flagged = [
        (code, int(count))
        for code, count in code_counts.items()
        if not _is_typical_stockcode(code)
    ]
    if not flagged:
        print("  None found.")
    else:
        print(f"  {len(flagged)} anomalous value(s):")
        for code, count in flagged:
            example = _first_description(df, code)
            print(
                f'  StockCode "{_label(code)}" | rows: {count:,} '
                f"| example Description: {example}"
            )

    print()
    print("=== 15 most frequent Description values where Price == 0 ===")
    zero_price_descriptions = (
        df.loc[df["Price"] == 0, "Description"]
        .value_counts(dropna=False)
        .head(15)
    )
    if zero_price_descriptions.empty:
        print("  No rows with Price == 0.")
    else:
        for rank, (description, count) in enumerate(
            zero_price_descriptions.items(), start=1
        ):
            print(f"  {rank:>2}. {_one_line(description)}: {count:,}")

    invoice_starts_with_c = df["Invoice"].astype(str).str.startswith("C")
    quantity_negative = df["Quantity"] < 0
    quantity_positive = df["Quantity"] > 0

    print()
    print("=== Quantity < 0 but Invoice does NOT start with 'C' ===")
    print(f"  count: {int((quantity_negative & ~invoice_starts_with_c).sum())}")

    print()
    print("=== Invoice starts with 'C' but Quantity > 0 ===")
    print(f"  count: {int((invoice_starts_with_c & quantity_positive).sum())}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _main()
