"""S3 ObjectStore implementation using boto3."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import boto3
from botocore.exceptions import ClientError

from oxivault.errors import (
    ObjectConflictError,
    ObjectNotFoundError,
    ObjectReadLimitError,
    StoreError,
)
from oxivault.store.base import _SENTINEL, StatInfo, validate_key

if TYPE_CHECKING:
    import builtins


class S3Store:
    """ObjectStore implementation backed by an S3-compatible bucket."""

    def __init__(  # noqa: PLR0913
        self,
        bucket: str,
        prefix: str = "",
        *,
        client: object | None = None,
        endpoint_url: str | None = None,
        region_name: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        """Initialize S3Store.

        Args:
            bucket: Name of S3 bucket.
            prefix: Key prefix within the bucket (e.g. 'vault/').
            client: Optional pre-configured boto3 S3 client.
            endpoint_url: Optional endpoint URL for MinIO, Cloudflare R2, Garage, etc.
            region_name: AWS region name.
            aws_access_key_id: Optional access key.
            aws_secret_access_key: Optional secret key.
        """
        self.bucket = bucket
        clean_prefix = prefix.strip("/")
        self.prefix = (clean_prefix + "/") if clean_prefix else ""

        if client is not None:
            self._client: Any = client
        else:
            self._client = cast(
                "Any",
                boto3.client(
                    "s3",
                    endpoint_url=endpoint_url,
                    region_name=region_name,
                    aws_access_key_id=aws_access_key_id,
                    aws_secret_access_key=aws_secret_access_key,
                ),
            )

    def _s3_key(self, key: str) -> str:
        clean = validate_key(key)
        return f"{self.prefix}{clean}"

    def _strip_prefix(self, s3_key: str) -> str:
        if self.prefix and s3_key.startswith(self.prefix):
            return s3_key[len(self.prefix) :]
        return s3_key

    def list(self, prefix: str = "") -> builtins.list[str]:
        """List keys matching prefix in sorted order."""
        full_prefix = f"{self.prefix}{prefix}"
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []

        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if not key.endswith("/"):
                        rel_key = self._strip_prefix(key)
                        keys.append(rel_key)
        except ClientError as err:
            raise StoreError(f"Failed to list S3 objects: {err}") from err

        keys.sort()
        return keys

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes:
        """Get object bytes, optionally bounding read size."""
        s3_key = self._s3_key(key)
        if max_bytes is not None:
            return self._get_bounded(key=key, s3_key=s3_key, max_bytes=max_bytes)
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=s3_key)
            return resp["Body"].read()
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                raise ObjectNotFoundError(key) from err
            raise StoreError(f"Failed to get S3 object {key}: {err}") from err

    def _get_bounded(self, *, key: str, s3_key: str, max_bytes: int) -> bytes:
        """Get object bytes with an upper bound."""
        try:
            # Read max_bytes + 1 to detect overflow
            resp = self._client.get_object(
                Bucket=self.bucket,
                Key=s3_key,
                Range=f"bytes=0-{max_bytes}",
            )
            data = resp["Body"].read()
            # If Content-Range or size indicates more bytes
            content_range = resp.get("ContentRange")
            if content_range:
                # Content-Range: bytes 0-5/10
                total_size = int(content_range.split("/")[-1])
                if total_size > max_bytes:
                    raise ObjectReadLimitError(key, max_bytes, total_size)
            elif len(data) > max_bytes:
                raise ObjectReadLimitError(key, max_bytes, len(data))
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                raise ObjectNotFoundError(key) from err
            if code in ("InvalidRange", "416"):
                return self._handle_invalid_range(key=key, s3_key=s3_key)
            raise StoreError(f"Failed to get S3 object {key}: {err}") from err
        else:
            return data

    def _handle_invalid_range(self, *, key: str, s3_key: str) -> bytes:
        """Handle bounded-range reads for edge cases like zero-byte objects."""
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=s3_key)
        except ClientError as head_err:
            head_code = head_err.response.get("Error", {}).get("Code")
            if head_code in ("NoSuchKey", "404"):
                raise ObjectNotFoundError(key) from head_err
            raise StoreError(f"Failed to get S3 object {key}: {head_err}") from head_err
        if head.get("ContentLength", 0) == 0:
            return b""
        raise StoreError(f"Failed to get S3 object {key}: invalid range for non-empty object")

    def put(
        self,
        key: str,
        data: bytes,
        *,
        expected_etag: str | object | None = _SENTINEL,
    ) -> str:
        """Put object data atomically and return new ETag."""
        s3_key = self._s3_key(key)
        kwargs: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": s3_key,
            "Body": data,
        }

        if expected_etag is None:
            # Create-only precondition
            kwargs["IfNoneMatch"] = "*"
        elif isinstance(expected_etag, str):
            # Optimistic concurrency check
            # S3 ETags might be quoted or unquoted; normalize
            clean_etag = expected_etag.strip('"')
            kwargs["IfMatch"] = f'"{clean_etag}"'

        try:
            resp = self._client.put_object(**kwargs)
            return resp["ETag"].strip('"')
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code in ("PreconditionFailed", "412"):
                raise ObjectConflictError(key) from err
            raise StoreError(f"Failed to put S3 object {key}: {err}") from err

    def delete(self, key: str) -> None:
        """Delete object. No-op if key does not exist."""
        s3_key = self._s3_key(key)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=s3_key)
        except ClientError as err:
            raise StoreError(f"Failed to delete S3 object {key}: {err}") from err

    def stat(self, key: str) -> StatInfo:
        """Return object metadata."""
        s3_key = self._s3_key(key)
        try:
            resp = self._client.head_object(Bucket=self.bucket, Key=s3_key)
            etag = resp["ETag"].strip('"')
            size = resp["ContentLength"]
            last_modified = resp.get("LastModified")
            return StatInfo(etag=etag, size=size, last_modified=last_modified)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404"):
                raise ObjectNotFoundError(key) from err
            raise StoreError(f"Failed to stat S3 object {key}: {err}") from err

    def exists(self, key: str) -> bool:
        """Check if object exists."""
        try:
            self.stat(key)
        except ObjectNotFoundError:
            return False
        else:
            return True

    def presign_put(self, key: str, *, expires_seconds: int) -> str:
        """Generate a presigned PUT URL for direct browser uploads."""
        s3_key = self._s3_key(key)
        if expires_seconds <= 0:
            raise ValueError("expires_seconds must be > 0")
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": s3_key},
            ExpiresIn=expires_seconds,
        )

    def presign_get(self, key: str, *, expires_seconds: int) -> str:
        """Generate a presigned GET URL for temporary private downloads."""
        s3_key = self._s3_key(key)
        if expires_seconds <= 0:
            raise ValueError("expires_seconds must be > 0")
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": s3_key},
            ExpiresIn=expires_seconds,
        )

    def copy_verified(
        self,
        source_key: str,
        target_key: str,
        *,
        expected_etag: str,
        target_bucket: str | None = None,
    ) -> str:
        """Copy object only if source ETag still matches."""
        src = self._s3_key(source_key)
        dst = self._s3_key(target_key)
        bucket = target_bucket or self.bucket
        clean_etag = expected_etag.strip('"')
        try:
            resp = self._client.copy_object(
                Bucket=bucket,
                Key=dst,
                CopySource={"Bucket": self.bucket, "Key": src},
                CopySourceIfMatch=clean_etag,
            )
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code")
            if code in ("PreconditionFailed", "412"):
                raise ObjectConflictError(source_key, "Source object changed before copy") from err
            if code in ("NoSuchKey", "404"):
                raise ObjectNotFoundError(source_key) from err
            raise StoreError(f"Failed to copy S3 object {source_key} -> {target_key}: {err}") from err
        else:
            return resp["CopyObjectResult"]["ETag"].strip('"')
