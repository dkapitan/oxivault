"""Local write failure and bounded I/O regressions."""

from pathlib import Path
from unittest.mock import patch

import pytest

from oxivault.errors import ObjectReadLimitError
from oxivault.store.local import LocalDirStore


def test_failed_replace_removes_temporary_file(tmp_path):
    store = LocalDirStore(tmp_path)
    store.put("note.md", b"original")
    with patch.object(Path, "replace", side_effect=OSError("disk full")), pytest.raises(OSError, match="disk full"):
        store.put("note.md", b"new")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["note.md"]
    assert store.get("note.md") == b"original"


def test_failed_write_removes_temporary_file(tmp_path):
    store = LocalDirStore(tmp_path)
    with (
        patch("tempfile._TemporaryFileWrapper.write", create=True, side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        store.put("note.md", b"new")
    assert list(tmp_path.iterdir()) == []


def test_unconditional_write_does_not_read_old_contents(tmp_path):
    store = LocalDirStore(tmp_path)
    store.put("note.md", b"original")
    with patch.object(store, "_compute_etag", side_effect=AssertionError("unnecessary read")):
        store.put("note.md", b"replacement")
    assert store.get("note.md") == b"replacement"


def test_bounded_get_handles_growth_after_stat(tmp_path):
    from io import BytesIO

    store = LocalDirStore(tmp_path)
    store.put("note.md", b"x")
    with (
        patch.object(Path, "open", return_value=BytesIO(b"abcdefgh")),
        pytest.raises(ObjectReadLimitError),
    ):
        store.get("note.md", max_bytes=4)
