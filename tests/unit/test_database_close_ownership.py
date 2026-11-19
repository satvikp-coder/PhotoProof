"""A failed cross-thread close cannot silently abandon a live SQLite connection."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from photoproof.contracts.exceptions import DatabaseError
from photoproof.core.database.store import Database, open_connection_count


def test_wrong_thread_close_reports_failure_and_retains_owner_cleanup(tmp_path):
    db = Database.open(tmp_path / "owned.db")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(db.close)
            with pytest.raises(DatabaseError, match="owning thread"):
                future.result(timeout=5)
        assert open_connection_count() == 1
        assert not db._closed
        db.create_run("still-owned", mode="single", profile="standard", cache_key=None)
        db.close()
        assert open_connection_count() == 0 and db._closed
    finally:
        # The old implementation marks itself closed after swallowing the error.
        # Close the actual connection explicitly so the failing regression is safe.
        db._conn.close()
        db.close()
