"""Console smoke test for ``db.transform.transform_raw_data``.

Loads the raw dataset exactly like ``scripts/profile_dataset.py`` (reusing its
``find_dataset_file`` and ``load_dataset`` so file detection and the
UTF-8/latin1 encoding fallback stay in one place), runs every raw row through
``transform_raw_data``, and prints a verification report: the resulting shape,
the ``line_item_type`` value counts, confirmation that no ``TEST001`` /
``TEST002`` rows remain, and the first 5 transformed rows.

Transformation only -- nothing is written to disk and no database is touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the project root importable so the ``db`` package resolves, and the
# scripts directory importable so the sibling profile modules resolve. This
# mirrors how scripts/profile_anomalies.py and scripts/profile_stockcodes.py
# reuse profile_dataset's loading helpers.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = Path(__file__).resolve().parent
for _path in (str(_PROJECT_ROOT), str(_SCRIPT_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pandas as pd  # noqa: E402

from db.transform import transform_raw_data  # noqa: E402
from profile_dataset import find_dataset_file, load_dataset  # noqa: E402

_TEST_STOCK_CODES = {"TEST001", "TEST002"}


def _count_test_codes(series: pd.Series) -> int:
    """Count rows whose trimmed, uppercased value is a TEST001/TEST002 code."""
    return int(
        series.astype("string").str.strip().str.upper().isin(_TEST_STOCK_CODES).sum()
    )


def _main() -> None:
    dataset_path = find_dataset_file()
    raw_df, _ = load_dataset(dataset_path)

    raw_test_rows = _count_test_codes(raw_df["StockCode"])
    transformed = transform_raw_data(raw_df)
    remaining_test_rows = _count_test_codes(transformed["stock_code"])

    print(f"Dataset file: {dataset_path.name}")
    print(f"Raw shape: {raw_df.shape[0]:,} rows x {raw_df.shape[1]} columns")
    print()
    print("=== Resulting shape ===")
    print(f"{transformed.shape[0]:,} rows x {transformed.shape[1]} columns")
    print()
    print("=== line_item_type value counts ===")
    for label, count in transformed["line_item_type"].value_counts().items():
        print(f"  {label:<12} {count:>12,}")
    print()
    print("=== TEST001/TEST002 removal check ===")
    print(f"  Rows matching TEST001/TEST002 in raw data: {raw_test_rows:,}")
    print(f"  Rows matching TEST001/TEST002 after transform: {remaining_test_rows:,}")
    print(f"  Confirmed: no TEST001/TEST002 rows remain -> {remaining_test_rows == 0}")
    print()
    print("=== First 5 transformed rows ===")
    print(transformed.head(5).to_string(index=False))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _main()
