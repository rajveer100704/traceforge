"""Object storage adapters: local filesystem (dev/test) and S3-compatible (MinIO/AWS).

Keys are content-addressed or versioned prefixes; objects are written once (immutable).
Re-writing an existing key with different bytes is rejected.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from traceforge.contracts.common import ArtifactRef, ConflictError, NotFoundError, sha256_bytes


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise ValueError(f"invalid object key: {key!r}")
    return key


def content_key(prefix: str, data: bytes, suffix: str = "") -> str:
    digest = sha256_bytes(data)
    return f"{prefix.strip('/')}/{digest[:2]}/{digest}{suffix}"


class LocalObjectStore:
    backend_name = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _check_key(key)).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("key escapes storage root")
        return path

    def put_bytes(
        self, key: str, data: bytes, media_type: str = "application/octet-stream"
    ) -> ArtifactRef:
        path = self._path(key)
        digest = sha256_bytes(data)
        if path.exists():
            if sha256_bytes(path.read_bytes()) != digest:
                raise ConflictError(
                    f"object {key} is immutable and already exists with other content"
                )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:8]}")
            tmp.write_bytes(data)
            tmp.replace(path)  # atomic publish; no partially-written objects
        return ArtifactRef(
            artifact_id=uuid.uuid4(),
            uri=self.uri_for(key),
            sha256=digest,
            size_bytes=len(data),
            media_type=media_type,
        )

    def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise NotFoundError(f"object {key} not found")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def uri_for(self, key: str) -> str:
        return f"local://{key}"

    def local_path(self, key: str) -> Path:
        return self._path(key)


class S3ObjectStore:
    backend_name = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str = "us-east-1",
        client: Any = None,
        create_bucket: bool = False,
    ) -> None:
        import boto3  # local import keeps boto3 out of core import paths

        self.bucket = bucket
        self._client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )
        if create_bucket:
            existing = {b["Name"] for b in self._client.list_buckets().get("Buckets", [])}
            if bucket not in existing:
                self._client.create_bucket(Bucket=bucket)

    def put_bytes(
        self, key: str, data: bytes, media_type: str = "application/octet-stream"
    ) -> ArtifactRef:
        _check_key(key)
        digest = sha256_bytes(data)
        if self.exists(key):
            head = self._client.head_object(Bucket=self.bucket, Key=key)
            if head.get("Metadata", {}).get("sha256") != digest:
                raise ConflictError(
                    f"object {key} is immutable and already exists with other content"
                )
        else:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                ContentType=media_type,
                Metadata={"sha256": digest},
            )
        return ArtifactRef(
            artifact_id=uuid.uuid4(),
            uri=self.uri_for(key),
            sha256=digest,
            size_bytes=len(data),
            media_type=media_type,
        )

    def get_bytes(self, key: str) -> bytes:
        _check_key(key)
        try:
            obj = self._client.get_object(Bucket=self.bucket, Key=key)
        except self._client.exceptions.NoSuchKey as exc:
            raise NotFoundError(f"object {key} not found") from exc
        data: bytes = obj["Body"].read()
        expected = obj.get("Metadata", {}).get("sha256")
        if expected and sha256_bytes(data) != expected:
            raise ConflictError(f"checksum mismatch for {key}")
        return data

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def uri_for(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"


def key_from_uri(uri: str) -> str:
    if uri.startswith("local://"):
        return uri[len("local://") :]
    if uri.startswith("s3://"):
        return uri.split("/", 3)[3]
    raise ValueError(f"unsupported uri {uri}")
