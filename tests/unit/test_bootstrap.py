from __future__ import annotations

from typing import TYPE_CHECKING

from spoldzielnia_ai.bootstrap import ensure_bucket

if TYPE_CHECKING:
    from minio.sseconfig import SSEConfig


class FakeBuckets:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.buckets = set(existing or ())
        self.encryption: dict[str, SSEConfig] = {}

    def bucket_exists(self, bucket_name: str) -> bool:
        return bucket_name in self.buckets

    def make_bucket(
        self, bucket_name: str, location: str | None = None, object_lock: bool = False
    ) -> None:
        self.buckets.add(bucket_name)

    def set_bucket_encryption(self, bucket_name: str, config: SSEConfig) -> None:
        self.encryption[bucket_name] = config

    def get_bucket_encryption(self, bucket_name: str) -> SSEConfig | None:
        return self.encryption.get(bucket_name)


def test_creates_missing_bucket_with_sse() -> None:
    fake = FakeBuckets()

    assert ensure_bucket(fake, "private", "us-east-1") is True
    assert "private" in fake.buckets
    rule = fake.encryption["private"].rule
    assert rule.sse_algorithm == "AES256"


def test_is_idempotent_and_reapplies_sse() -> None:
    fake = FakeBuckets(existing={"private"})

    assert ensure_bucket(fake, "private", "us-east-1") is False
    assert "private" in fake.encryption
