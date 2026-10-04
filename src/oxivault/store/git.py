"""Git ObjectStore implementation using dulwich."""

from __future__ import annotations

import stat as stat_mod
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from dulwich.index import build_index_from_tree
from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo

from oxivault.errors import (
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectReadLimitError,
)
from oxivault.store.base import _SENTINEL, StatInfo, validate_key

if TYPE_CHECKING:
    import builtins


class GitStore:
    """ObjectStore implementation backed by a Git repository (bare or working tree)."""

    def __init__(self, repo_path: Path | str, *, bare: bool = True) -> None:
        """Initialize GitStore with a repository path."""
        self.path = Path(repo_path)
        self.bare = bare
        self.repo = Repo(str(self.path))
        self._write_lock = threading.Lock()

    @classmethod
    def init_bare(cls, path: Path | str) -> GitStore:
        """Initialize a new bare git repo and return GitStore."""
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        Repo.init_bare(str(p))
        return cls(p, bare=True)

    @classmethod
    def init_worktree(cls, path: Path | str) -> GitStore:
        """Initialize a new normal working-tree git repo and return GitStore."""
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        Repo.init(str(p))
        return cls(p, bare=False)

    def _get_head_commit(self) -> Commit | None:
        try:
            head_ref = self.repo.head()
            obj = self.repo.get_object(head_ref)
            return obj if isinstance(obj, Commit) else None
        except (KeyError, IndexError):
            return None

    def _get_tree(self, commit: Commit | None) -> Tree | None:
        if commit is None:
            return None
        obj = self.repo.get_object(commit.tree)
        return obj if isinstance(obj, Tree) else None

    def _lookup_path(self, path: str) -> tuple[bytes, int] | None:
        commit = self._get_head_commit()
        if commit is None:
            return None
        tree = self._get_tree(commit)
        if tree is None:
            return None

        segments = validate_key(path).encode("utf-8").split(b"/")
        curr_tree = tree
        for i, seg in enumerate(segments):
            try:
                mode, sha = curr_tree[seg]
            except KeyError:
                return None
            if i == len(segments) - 1:
                return sha, mode
            obj = self.repo.get_object(cast("Any", sha))
            if not isinstance(obj, Tree):
                return None
            curr_tree = obj
        return None

    def list(self, prefix: str = "") -> builtins.list[str]:
        """List keys matching prefix."""
        commit = self._get_head_commit()
        if commit is None:
            return []

        tree = self._get_tree(commit)
        if tree is None:
            return []

        results: list[str] = []

        def walk(t: Tree, cur_prefix: str) -> None:
            for item in t.items():
                name = item.path.decode("utf-8")
                path = f"{cur_prefix}{name}" if not cur_prefix else f"{cur_prefix}/{name}"
                mode = item.mode
                if stat_mod.S_ISDIR(mode):
                    sub = self.repo.get_object(cast("Any", item.sha))
                    if isinstance(sub, Tree):
                        walk(sub, path)
                elif path.startswith(prefix):
                    results.append(path)

        walk(tree, "")
        results.sort()
        return results

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes:
        """Get object bytes."""
        res = self._lookup_path(key)
        if res is None:
            raise ObjectNotFoundError(key)
        sha, _ = res
        blob = self.repo.get_object(cast("Any", sha))
        if not isinstance(blob, Blob):
            raise ObjectNotFoundError(key)

        data = blob.data
        if max_bytes is not None and len(data) > max_bytes:
            raise ObjectReadLimitError(key, max_bytes, len(data))
        return data

    def stat(self, key: str) -> StatInfo:
        """Stat object (ETag is blob SHA1)."""
        res = self._lookup_path(key)
        if res is None:
            raise ObjectNotFoundError(key)
        sha, _ = res
        blob = self.repo.get_object(cast("Any", sha))
        if not isinstance(blob, Blob):
            raise ObjectNotFoundError(key)
        etag = sha.decode("ascii") if isinstance(sha, bytes) else str(sha)
        return StatInfo(etag=etag, size=len(blob.data), last_modified=None)

    def exists(self, key: str) -> bool:
        """Check if object exists."""
        return self._lookup_path(key) is not None

    def history(self, key: str) -> builtins.list[dict[str, str | int]]:
        """Return commit history for an object."""
        records: list[dict[str, str | int]] = []
        clean_key = validate_key(key)
        try:
            walker = self.repo.get_walker(paths=[clean_key.encode("utf-8")])
        except KeyError:
            return []

        for entry in walker:
            commit = entry.commit
            tree = self._get_tree(commit)
            if tree is None:
                continue
            # Look up path in this tree
            segments = validate_key(key).encode("utf-8").split(b"/")
            curr = tree
            found = False
            sha = b""
            for i, seg in enumerate(segments):
                if seg not in curr:
                    break
                _, s = curr[seg]
                if i == len(segments) - 1:
                    found = True
                    sha = s
                    break
                sub = self.repo.get_object(cast("Any", s))
                if isinstance(sub, Tree):
                    curr = sub
                else:
                    break
            if found:
                records.append(
                    {
                        "commit": commit.id.decode("ascii"),
                        "etag": sha.decode("ascii"),
                        "message": commit.message.decode("utf-8", errors="replace"),
                        "time": commit.commit_time,
                    }
                )
        return records

    def put(
        self,
        key: str,
        data: bytes,
        *,
        expected_etag: str | object | None = _SENTINEL,
    ) -> str:
        """Put object with tree surgery and commit."""
        with self._write_lock:
            clean_key = validate_key(key)
            current = self._lookup_path(clean_key)

            if expected_etag is None:
                if current is not None:
                    raise ObjectConflictError(clean_key, "Object already exists")
            elif isinstance(expected_etag, str):
                if current is None:
                    raise ObjectConflictError(clean_key, "Object not found for expected etag")
                curr_etag = current[0].decode("ascii")
                if curr_etag != expected_etag:
                    raise ObjectConflictError(clean_key, f"ETag mismatch: current {curr_etag} != {expected_etag}")

            # Create blob
            blob = Blob.from_string(data)
            self.repo.object_store.add_object(blob)
            blob_sha = blob.id

            # Update tree structure
            head_commit = self._get_head_commit()
            old_tree = self._get_tree(head_commit) if head_commit else Tree()
            old_head = head_commit.id if head_commit else None
            new_tree_sha = self._update_tree(old_tree, clean_key.split("/"), 0o100644, blob_sha)

            # Create commit
            commit = Commit()
            commit.tree = new_tree_sha
            commit.parents = [head_commit.id] if head_commit else []
            author = b"OxiVault <oxivault@local>"
            commit.author = commit.committer = author
            now = int(time.time())
            commit.author_time = commit.commit_time = now
            commit.author_timezone = commit.commit_timezone = 0
            commit.message = f"oxivault: put {clean_key}".encode()
            self.repo.object_store.add_object(commit)

            if not cast("Any", self.repo.refs).set_if_equals(b"HEAD", old_head, commit.id):
                raise ObjectConflictError(clean_key, "HEAD changed concurrently")

            # If working tree, write out file to disk and refresh index
            if not self.bare:
                disk_path = self.path / clean_key
                disk_path.parent.mkdir(parents=True, exist_ok=True)
                disk_path.write_bytes(data)
                build_index_from_tree(str(self.path), self.repo.index_path(), self.repo.object_store, commit.tree)

            return blob_sha.decode("ascii")

    def _update_tree(self, tree: Tree | None, segments: builtins.list[str], mode: int, sha: bytes | None) -> bytes:
        t = Tree()
        if tree is not None:
            for item in tree.items():
                t.add(item.path, item.mode, cast("Any", item.sha))

        seg = segments[0].encode("utf-8")
        if len(segments) == 1:
            if sha is None:
                # delete
                if seg in t:
                    del t[seg]
            else:
                t.add(seg, mode, cast("Any", sha))
        else:
            # Subtree
            sub_sha = None
            if seg in t:
                _, s = t[seg]
                sub_sha = s
            sub_tree = self.repo.get_object(cast("Any", sub_sha)) if sub_sha else None
            new_sub_sha = self._update_tree(sub_tree if isinstance(sub_tree, Tree) else None, segments[1:], mode, sha)
            t.add(seg, stat_mod.S_IFDIR, cast("Any", new_sub_sha))

        self.repo.object_store.add_object(t)
        return t.id

    def delete(self, key: str) -> None:
        """Delete object and commit tree update."""
        with self._write_lock:
            clean_key = validate_key(key)
            if not self.exists(clean_key):
                return

            head_commit = self._get_head_commit()
            old_tree = self._get_tree(head_commit) if head_commit else Tree()
            old_head = head_commit.id if head_commit else None
            new_tree_sha = self._update_tree(old_tree, clean_key.split("/"), 0, None)

            commit = Commit()
            commit.tree = new_tree_sha
            commit.parents = [head_commit.id] if head_commit else []
            author = b"OxiVault <oxivault@local>"
            commit.author = commit.committer = author
            now = int(time.time())
            commit.author_time = commit.commit_time = now
            commit.author_timezone = commit.commit_timezone = 0
            commit.message = f"oxivault: delete {clean_key}".encode()
            self.repo.object_store.add_object(commit)

            if not cast("Any", self.repo.refs).set_if_equals(b"HEAD", old_head, commit.id):
                raise ObjectConflictError(clean_key, "HEAD changed concurrently")

            if not self.bare:
                disk_path = self.path / clean_key
                if disk_path.exists():
                    disk_path.unlink()
                build_index_from_tree(str(self.path), self.repo.index_path(), self.repo.object_store, commit.tree)
