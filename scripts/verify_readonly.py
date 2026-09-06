"""Verify ``db.connect.connect_readonly`` is a real, driver-level read-only layer.

Run with the project interpreter from the project root::

    python scripts/verify_readonly.py            # tests data/processed/sentrasql.db
    python scripts/verify_readonly.py PATH_TO_DB # tests an explicit database path

The script opens ``data/processed/sentrasql.db`` (or the CLI override) with
``db.connect.connect_readonly`` and proves four things about that connection:

1. **Reads work.** A ``SELECT COUNT(*)`` and a sample-row ``SELECT`` return
   real data through the read-only connection.
2. **Writes are rejected by the driver.** An ``INSERT`` -- and, as a second
   statement class, a ``CREATE TABLE`` -- fail with
   ``sqlite3.OperationalError: attempt to write a readonly database``. The
   rejection comes from the connection itself (``mode=ro``), not from the
   wording of the SQL, which is a perfectly valid write.
3. **Reads still work after a rejected write** (the connection is not wedged).
4. **The database file on disk is genuinely unmodified.** Row counts in both
   tables, the file size, a full SHA-256 of the file, and the contents of the
   database's directory are captured before and after the attempt and compared.

The script never executes a successful write; every connection it opens is
either read-only or used for SELECTs only, so a PASS run leaves the database
exactly as it found it.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

# Make the project root importable so the ``db`` package resolves. This mirrors
# the sys.path handling used by the other run_* scripts.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from db.connect import connect_readonly  # noqa: E402

_PROCESSED_DB = _PROJECT_ROOT / "data" / "processed" / "sentrasql.db"

_INSERT_SQL = (
    "INSERT INTO transactions (invoice_id, is_cancelled_invoice, stock_code,"
    " line_item_type, quantity, unit_price, country, invoice_timestamp)"
    " VALUES ('RO_TEST', 0, 'TEST', 'product', 1, 1.0, 'Testland',"
    " '2026-09-06 12:00:00')"
)


def _sha256(path: Path) -> str:
    """Return the hex SHA-256 of ``path``, streamed in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_state(folder: Path) -> list[str]:
    """Return the sorted file names in ``folder`` (sidecar detection)."""
    return sorted(entry.name for entry in folder.iterdir())


def _rw_table_counts(db_path: Path) -> tuple[int, int]:
    """Return ``(transactions, country_timezones)`` row counts via a normal connection."""
    conn = sqlite3.connect(str(db_path))
    try:
        transactions = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        timezones = conn.execute(
            "SELECT COUNT(*) FROM country_timezones"
        ).fetchone()[0]
    finally:
        conn.close()
    return transactions, timezones


def _attempt_write(conn: sqlite3.Connection, label: str, sql: str) -> None:
    """Execute ``sql`` on ``conn`` and report the driver-level rejection."""
    print(f"  attempting {label} ...")
    try:
        conn.execute(sql)
        conn.commit()
    except sqlite3.Error as exc:
        print(f"    REJECTED ({type(exc).__name__}): {exc}")
    else:  # pragma: no cover - impossible on a mode=ro connection
        raise AssertionError(
            f"{label} unexpectedly succeeded on a supposedly read-only connection"
        )


def _main() -> None:
    args = sys.argv[1:]
    db_path = Path(args[0]) if args else _PROCESSED_DB
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    print(f"Database under test : {db_path}")
    print(f"Interpreter         : CPython {sys.version.split()[0]}")
    print(f"sqlite3 driver      : {sqlite3.sqlite_version}")
    print()

    print("=== Approach chosen (and why) ===")
    print(
        "db.connect.connect_readonly opens the database with SQLite's URI "
        "filename facility and the mode=ro query parameter:"
    )
    print("    sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)")
    print(
        "mode=ro makes the driver open the file WITHOUT write permission, so SQLite"
    )
    print(
        "itself rejects every write statement at the driver level -- it is a real"
    )
    print(
        "enforcement layer, not a naming convention. The alternative of opening"
    )
    print(
        "normally and setting PRAGMA query_only=ON was considered and rejected:"
    )
    print(
        "that only toggles a per-connection flag on an already read-write file"
    )
    print("handle, which is a weaker guarantee than an OS-level read-only open.")
    print()

    print("=== 1. State BEFORE the failed write ===")
    txn_before, tz_before = _rw_table_counts(db_path)
    print(f"  transactions rows       : {txn_before:,}")
    print(f"  country_timezones rows  : {tz_before:,}")
    size_before = db_path.stat().st_size
    hash_before = _sha256(db_path)
    print(f"  file size (bytes)       : {size_before:,}")
    print(f"  file sha256             : {hash_before}")
    dir_before = _directory_state(db_path.parent)
    print(f"  data/processed files    : {dir_before}")
    print()

    print("=== 2. Read-only connection: reads still work ===")
    conn = connect_readonly(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        print(f"  SELECT COUNT(*) FROM transactions -> {total:,}")
        sample = conn.execute(
            "SELECT invoice_id, stock_code, quantity, unit_price, country "
            "FROM transactions LIMIT 1"
        ).fetchone()
        print(f"  sample row (LIMIT 1)              -> {sample}")
        print()

        print("=== 3. Read-only connection: write attempts are rejected ===")
        _attempt_write(
            conn,
            "INSERT (valid SQL, would succeed on a writable DB)",
            _INSERT_SQL,
        )
        _attempt_write(
            conn,
            "CREATE TABLE (a second, different statement class)",
            "CREATE TABLE _readonly_probe (x INTEGER)",
        )
        print()

        print("=== 4. Read-only connection: reads still work after rejection ===")
        total_after_reject = conn.execute(
            "SELECT COUNT(*) FROM transactions"
        ).fetchone()[0]
        print(f"  SELECT COUNT(*) FROM transactions -> {total_after_reject:,}")
        print()
    finally:
        conn.close()

    print("=== 5. State AFTER the failed write ===")
    txn_after, tz_after = _rw_table_counts(db_path)
    print(f"  transactions rows       : {txn_after:,}")
    print(f"  country_timezones rows  : {tz_after:,}")
    size_after = db_path.stat().st_size
    hash_after = _sha256(db_path)
    print(f"  file size (bytes)       : {size_after:,}")
    print(f"  file sha256             : {hash_after}")
    dir_after = _directory_state(db_path.parent)
    print(f"  data/processed files    : {dir_after}")
    print()

    print("=== Verdict ===")
    checks = {
        "transactions row count identical": txn_before == txn_after,
        "country_timezones row count identical": tz_before == tz_after,
        "file size identical": size_before == size_after,
        "file sha256 identical": hash_before == hash_after,
        "no new files appeared in data/processed": dir_before == dir_after,
        "reads still worked after rejected write": total_after_reject == txn_before,
    }
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    if not all(checks.values()):
        raise SystemExit(
            "FAIL: the database file changed -- read-only enforcement broken"
        )
    print()
    print(
        "PASS: write attempts were rejected at the driver level and the database "
        "file is byte-for-byte unmodified."
    )


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _main()
