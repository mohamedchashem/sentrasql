-- ============================================================================
-- SentraSQL — database schema
-- ============================================================================
-- Tables:
--   transactions       : one row per retail line item (post-preprocessing form).
--   country_timezones  : country -> IANA timezone lookup supporting the
--                        timezone-handling requirement documented in
--                        DESIGN_LOG.md section 1.6.
--
-- Conventions / notes:
--   * No indexes are defined yet — deferred until query patterns are known.
--   * country_timezones is intentionally NOT populated yet (schema only).
--   * The "value-domain" invariants on stock_code / line_item_type /
--     invoice_timestamp are preprocessing contracts (they live in the load
--     layer, not in this DDL), so they are documented as comments only.
-- ============================================================================

-- Line-item-level transaction data. One row per (invoice, stock code) line.
CREATE TABLE transactions (
    invoice_id           TEXT    NOT NULL,  -- invoice number (e.g. "536365"); "C" prefix marks a cancelled invoice
    is_cancelled_invoice BOOLEAN NOT NULL,  -- 1 when invoice_id carries the "C" cancellation flag, else 0
    stock_code           TEXT    NOT NULL,  -- stock code, whitespace-trimmed and case-normalized (uppercase)
    description          TEXT,              -- free-text product description; NULL when the source value is missing
    line_item_type       TEXT    NOT NULL,  -- one of: "product" | "fee" | "adjustment"
    quantity             INTEGER NOT NULL,  -- signed integer; negative = return (may be negative on non-C invoices)
    unit_price           REAL    NOT NULL,  -- per-unit price; 0 is valid (free samples / promotional items)
    customer_id          REAL,              -- NULL for guest / unknown customers
    country              TEXT    NOT NULL,  -- country name; matches the dataset's Country column values
    invoice_timestamp    TEXT    NOT NULL   -- ISO-8601 datetime string (e.g. "2009-12-01 07:45:00")
);

-- Country -> IANA timezone lookup table (see DESIGN_LOG.md section 1.6).
-- country values match the dataset's Country column; timezone is an IANA name
-- such as "Europe/London" or "Asia/Dubai". Populated in a later task.
CREATE TABLE country_timezones (
    country  TEXT NOT NULL PRIMARY KEY,  -- matches the dataset's Country column values
    timezone TEXT NOT NULL               -- IANA timezone name, e.g. "Europe/London"
);
