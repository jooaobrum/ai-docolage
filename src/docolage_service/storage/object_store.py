"""Thin wrapper around an S3-compatible object store (MinIO locally, S3 or
any S3-compatible store in production), configured entirely through
environment variables (see config.py).

Per spec user story 6 ("I want the object storage bucket to be created on
start if it is missing"), `ensure_bucket` is called from the API's
startup lifespan.

boto3's client is synchronous; every method here offloads the blocking
call to a thread so it composes cleanly with the async API/worker code
without needing a separate async S3 client dependency.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError


@dataclass
class ObjectStoreSettings:
    endpoint_url: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"
    use_ssl: bool = False


class ObjectStore:
    """A minimal upload/download/exists surface over one bucket."""

    def __init__(self, settings: ObjectStoreSettings) -> None:
        self._bucket = settings.bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key,
            aws_secret_access_key=settings.secret_key,
            region_name=settings.region,
            use_ssl=settings.use_ssl,
            config=BotoConfig(signature_version="s3v4"),
        )

    async def ensure_bucket(self) -> None:
        """Create the configured bucket if it doesn't already exist.

        Idempotent: safe to call on every API start.
        """
        await asyncio.to_thread(self._ensure_bucket_sync)

    def _ensure_bucket_sync(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)

    async def is_reachable(self) -> bool:
        """Best-effort liveness check used by /health. Never raises."""
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self._bucket)
            return True
        except (ClientError, BotoCoreError, Exception):  # noqa: BLE001
            return False

    async def put(self, key: str, body: bytes | BinaryIO, content_type: str | None = None) -> str:
        """Upload `body` under `key` and return the object's storage URI."""
        extra = {"ContentType": content_type} if content_type else {}
        await asyncio.to_thread(self._client.put_object, Bucket=self._bucket, Key=key, Body=body, **extra)
        return f"s3://{self._bucket}/{key}"

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)
