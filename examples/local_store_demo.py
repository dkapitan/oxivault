"""Simple runnable example demonstrating LocalDirStore usage."""

from __future__ import annotations

import tempfile
from pathlib import Path

from oxivault.store.local import LocalDirStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalDirStore(root_dir=Path(tmp_dir))
        print(f"Initialized store at {tmp_dir}")

        # Put a note
        note_key = "Recipes/hummus.md"
        etag = store.put(note_key, b"---\ntype: cul:Recipe\n---\nDelicious hummus recipe.\n")
        print(f"Put {note_key} with ETag {etag[:8]}...")

        # Read the note back
        content = store.get(note_key)
        print(f"Read back {len(content)} bytes:")
        print(content.decode("utf-8"))

        # List notes
        keys = store.list("Recipes/")
        print(f"Listed keys in Recipes/: {keys}")

        # Stat
        info = store.stat(note_key)
        print(f"Stat: size={info.size}, etag={info.etag[:8]}, last_modified={info.last_modified}")


if __name__ == "__main__":
    main()
