"""Console-only anomaly scan of the raw dataset in ``data/raw``.

The dataset is located and loaded exactly like ``scripts/profile_dataset.py``
by reusing its ``find_dataset_file`` and ``load_dataset`` functions (file
detection + UTF-8/latin1 encoding fallback). No cleaning or transformation is
applied -- this is investigation only, and nothing is written to disk.
"""

from __future__ import annotations

import sys

from profile_dataset import find_dataset_file, load_dataset


def _print_section(title: str) -> None:
    print(f"=== {title} ===")


def _main() -> None:
    dataset_path = find_dataset_file()
    df, _ = load_dataset(dataset_path)

    print(f"Dataset: {dataset_path.name} ({len(df):,} rows)")
    print()

    for column in ("Quantity", "Price"):
        series = df[column]
        _print_section(column)
        print(f"min: {series.min()}")
        print(f"max: {series.max()}")
        print(f"negative values: {int((series < 0).sum())}")
        print(f"zero values: {int((series == 0).sum())}")
        print()

    invoice_starts_with_c = df["Invoice"].astype(str).str.startswith("C")
    _print_section("Invoice (values starting with 'C')")
    print(f"count: {int(invoice_starts_with_c.sum())}")
    print("3 example rows containing one:")
    examples = df.loc[invoice_starts_with_c].head(3)
    for _, row in examples.iterrows():
        print("  " + " | ".join(f"{name}={row[name]}" for name in df.columns))
    print()

    _print_section("StockCode (20 most frequent values)")
    top_codes = df["StockCode"].value_counts().head(20)
    for rank, (code, count) in enumerate(top_codes.items(), start=1):
        print(f"{rank:>2}. {code}: {count:,}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _main()
