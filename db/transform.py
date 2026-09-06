"""Deterministic raw-to-schema data transformation for the ``transactions`` table.

This module is the preprocessing layer that sits between the raw retail CSV
(loaded via ``scripts/profile_dataset.py``'s ``load_dataset``) and the database
schema in ``db/schema.sql``. It is intentionally pure: a DataFrame goes in, a
new DataFrame comes out -- no database connection, no SQL, no file I/O.

The transformation is described in detail in DESIGN_LOG.md sections 2-3 and
consists of, in order:

1. Drop rows whose ``StockCode`` (whitespace-trimmed, case-insensitive) is
   ``TEST001`` / ``TEST002`` (literal test data, no business meaning).
2. Whitespace-trim and uppercase-normalize ``StockCode`` for every remaining row.
3. Add ``line_item_type`` from the hardcoded confirmed code lists
   (fee / adjustment / adjustment-for-``GIFT_0001*`` / product).
4. Add ``is_cancelled_invoice`` (True when ``Invoice`` starts with ``"C"``).
5. Convert ``InvoiceDate`` to a real datetime and then to an ISO-8601 string
   (``YYYY-MM-DDTHH:MM:SS``) for SQLite storage.
6. Rename raw columns to the ``transactions`` schema column names.
"""

from __future__ import annotations

import pandas as pd

# Exact hardcoded StockCode lists confirmed during data investigation
# (DESIGN_LOG.md section 2.3). Codes are stored uppercase after step 2, so the
# membership tests below compare against uppercase values.
FEE_STOCK_CODES = frozenset(
    {"POST", "DOT", "C2", "BANK CHARGES", "AMAZONFEE", "CRUK"}
)
ADJUSTMENT_STOCK_CODES = frozenset({"M", "D", "S", "ADJUST", "ADJUST2", "B"})

# Any stock code starting with this prefix is an adjustment line item
# (case-insensitive test applied after the uppercase-normalization step).
GIFT_CODE_PREFIX = "GIFT_0001"

# Raw CSV column name -> transactions schema column name.
_RENAME_COLUMNS = {
    "Invoice": "invoice_id",
    "StockCode": "stock_code",
    "Description": "description",
    "Quantity": "quantity",
    "Price": "unit_price",
    "Customer ID": "customer_id",
    "Country": "country",
    "InvoiceDate": "invoice_timestamp",
}

# Exact output column order required by the schema-ready contract.
_OUTPUT_COLUMN_ORDER = [
    "invoice_id",
    "is_cancelled_invoice",
    "stock_code",
    "description",
    "line_item_type",
    "quantity",
    "unit_price",
    "customer_id",
    "country",
    "invoice_timestamp",
]


def transform_raw_data(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Transform a raw retail DataFrame into the schema-ready shape.

    Takes a DataFrame in the exact shape produced by
    ``scripts/profile_dataset.py``'s ``load_dataset`` (columns: Invoice,
    StockCode, Description, Quantity, InvoiceDate, Price, Customer ID, Country)
    and returns a new DataFrame with the six transformations listed in the
    module docstring applied, containing exactly the ``_OUTPUT_COLUMN_ORDER``
    columns. The input DataFrame is never mutated.
    """
    df = raw_df.copy()

    # 1. Drop literal test rows (trimmed StockCode == TEST001/TEST002,
    #    case-insensitive). NaN codes compare as False and are kept.
    test_code_mask = (
        df["StockCode"]
        .astype("string")
        .str.strip()
        .str.upper()
        .isin({"TEST001", "TEST002"})
    )
    df = df.loc[~test_code_mask].copy()

    # 2. Whitespace-trim and uppercase-normalize StockCode on remaining rows.
    df["StockCode"] = (
        df["StockCode"].astype("string").str.strip().str.upper()
    )

    # 3. Classify every row's line_item_type from the hardcoded code lists.
    df["line_item_type"] = "product"
    df.loc[df["StockCode"].isin(FEE_STOCK_CODES), "line_item_type"] = "fee"
    df.loc[
        df["StockCode"].isin(ADJUSTMENT_STOCK_CODES)
        | df["StockCode"].str.upper().str.startswith(
            GIFT_CODE_PREFIX, na=False
        ),
        "line_item_type",
    ] = "adjustment"

    # 4. Flag cancelled invoices: Invoice (as a string) starts with "C".
    df["is_cancelled_invoice"] = df["Invoice"].astype(str).str.startswith(
        "C", na=False
    )

    # 5. Real datetime first, then ISO-8601 string for SQLite storage.
    df["InvoiceDate"] = pd.to_datetime(
        df["InvoiceDate"]
    ).dt.strftime("%Y-%m-%dT%H:%M:%S")

    # 6. Rename raw columns to the schema names and keep the exact order.
    df = df.rename(columns=_RENAME_COLUMNS)
    return df.loc[:, _OUTPUT_COLUMN_ORDER]
