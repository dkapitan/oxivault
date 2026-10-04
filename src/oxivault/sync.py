"""Obsidian sync engine using local state manifest and conditional writes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from oxivault.errors import ObjectConflictError

if TYPE_CHECKING:
    from oxivault.store.base import ObjectStore

STATE_FILE = ".oxivault/state.json"


@dataclass(frozen=True)
class SyncResult:
    """Outcome of a pull or push sync operation."""

    downloaded: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


class SyncEngine:
    """Manages two-way sync between a local Obsidian vault and a remote store."""

    def __init__(self, local: ObjectStore, remote: ObjectStore) -> None:
        """Initialize SyncEngine with local and remote object stores."""
        self.local = local
        self.remote = remote

    @staticmethod
    def _content_hash(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _load_manifest(self) -> tuple[dict[str, str], dict[str, str]]:
        if not self.local.exists(STATE_FILE):
            return {}, {}
        raw = self.local.get(STATE_FILE)
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}, {}
        remote_etags = doc.get("remote_etags", {})
        local_hashes = doc.get("local_hashes", {})
        if isinstance(remote_etags, dict) and isinstance(local_hashes, dict):
            return remote_etags, local_hashes
        return {}, {}

    def _save_manifest(self, remote_etags: dict[str, str], local_hashes: dict[str, str]) -> None:
        data = {
            "version": 1,
            "remote_etags": remote_etags,
            "local_hashes": local_hashes,
        }
        content = json.dumps(data, indent=2).encode("utf-8")
        self.local.put(STATE_FILE, content)

    def pull(self) -> SyncResult:
        """Download remote updates into local vault (idempotent)."""
        remote_manifest, local_hashes = self._load_manifest()
        remote_keys = self.remote.list()
        downloaded: list[str] = []
        conflicts: list[str] = []

        new_remote_manifest = dict(remote_manifest)
        new_local_hashes = dict(local_hashes)
        for key in remote_keys:
            if key.startswith(".oxivault/"):
                continue
            remote_stat = self.remote.stat(key)
            local_exists = self.local.exists(key)
            local_data = self.local.get(key) if local_exists else b""
            local_hash = self._content_hash(local_data) if local_exists else None
            synced_local_hash = local_hashes.get(key)
            remote_changed = remote_manifest.get(key) != remote_stat.etag

            # Download if not present locally or remote etag changed from manifest
            if not local_exists:
                data = self.remote.get(key)
                self.local.put(key, data)
                downloaded.append(key)
                new_local_hashes[key] = self._content_hash(data)
            elif remote_changed:
                local_changed = synced_local_hash is None or synced_local_hash != local_hash
                if local_changed:
                    conflicts.append(key)
                    self.local.put(self._conflict_name(key), local_data)
                data = self.remote.get(key)
                self.local.put(key, data)
                downloaded.append(key)
                new_local_hashes[key] = self._content_hash(data)
            new_remote_manifest[key] = remote_stat.etag

        self._save_manifest(new_remote_manifest, new_local_hashes)
        return SyncResult(downloaded=downloaded, conflicts=conflicts)

    def _handle_existing_remote(
        self,
        key: str,
        local_data: bytes,
        local_hash: str,
        last_synced_etag: str | None,
        context: tuple[dict[str, str], dict[str, str], list[str], list[str]],
    ) -> None:
        new_remote_manifest, new_local_hashes, uploaded, conflicts = context
        remote_stat = self.remote.stat(key)
        if remote_stat.etag != last_synced_etag:
            if last_synced_etag is None:
                conflicts.append(key)
                self.local.put(self._conflict_name(key), local_data)
                return
            try:
                new_etag = self.remote.put(key, local_data, expected_etag=last_synced_etag)
                new_remote_manifest[key] = new_etag
                new_local_hashes[key] = local_hash
                uploaded.append(key)
            except ObjectConflictError:
                conflicts.append(key)
                self.local.put(self._conflict_name(key), local_data)
        elif new_local_hashes.get(key) != local_hash:
            try:
                new_etag = self.remote.put(key, local_data, expected_etag=last_synced_etag)
                new_remote_manifest[key] = new_etag
                new_local_hashes[key] = local_hash
                uploaded.append(key)
            except ObjectConflictError:
                conflicts.append(key)
                self.local.put(self._conflict_name(key), local_data)

    def push(self) -> SyncResult:
        """Upload local additions and edits to remote store with conflict detection."""
        remote_manifest, local_hashes = self._load_manifest()
        local_keys = self.local.list()
        uploaded: list[str] = []
        conflicts: list[str] = []

        new_remote_manifest = dict(remote_manifest)
        new_local_hashes = dict(local_hashes)
        for key in local_keys:
            if key.startswith(".oxivault/") or key.endswith(".conflict.md"):
                continue

            local_data = self.local.get(key)
            local_hash = self._content_hash(local_data)
            last_synced_etag = remote_manifest.get(key)

            if not self.remote.exists(key) and last_synced_etag is None:
                try:
                    new_etag = self.remote.put(key, local_data, expected_etag=None)
                    new_remote_manifest[key] = new_etag
                    new_local_hashes[key] = local_hash
                    uploaded.append(key)
                except ObjectConflictError:
                    conflicts.append(key)
                    self.local.put(self._conflict_name(key), local_data)
            elif self.remote.exists(key):
                self._handle_existing_remote(
                    key,
                    local_data,
                    local_hash,
                    last_synced_etag,
                    (new_remote_manifest, new_local_hashes, uploaded, conflicts),
                )

        self._save_manifest(new_remote_manifest, new_local_hashes)
        return SyncResult(uploaded=uploaded, conflicts=conflicts)

    @staticmethod
    def _conflict_name(key: str) -> str:
        if key.endswith(".md"):
            return key[:-3] + ".conflict.md"
        return key + ".conflict"
