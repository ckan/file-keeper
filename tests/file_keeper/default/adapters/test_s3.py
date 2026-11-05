from __future__ import annotations

import dataclasses
import os
from typing import Any

import pytest
from faker import Faker

import file_keeper as fk

try:
    import file_keeper.default.adapters.s3 as s3
except ImportError:
    pytest.skip("boto3 is not installed", allow_module_level=True)


from . import standard

Settings = s3.Settings
Storage = s3.S3Storage


@pytest.fixture
def storage(faker: Faker, storage_settings: dict[str, Any]):
    endpoint = os.getenv("FILE_KEEPER_TEST_MINIO_ENDPOINT")
    if not endpoint:
        pytest.skip("MinIO is not configured")

    settings: dict[str, Any] = {
        "name": "test",
        "bucket": "file-keeper",
        "path": faker.file_path(extension=[]),
        "key": os.getenv("FILE_KEEPER_TEST_MINIO_KEY"),
        "secret": os.getenv("FILE_KEEPER_TEST_MINIO_SECRET"),
        "endpoint": endpoint,
        "initialize": True,
    }
    settings.update(storage_settings)

    storage = Storage(settings)

    client = storage.settings.client

    result = client.list_objects_v2(Bucket=storage.settings.bucket)
    if items := result.get("Contents"):
        keys = [{"Key": obj["Key"]} for obj in items]  # pyright: ignore[reportTypedDictNotRequiredAccess]
        client.delete_objects(Bucket=storage.settings.bucket, Delete={"Objects": keys})  # pyright: ignore[reportArgumentType]

    result = client.list_multipart_uploads(Bucket=storage.settings.bucket)
    if items := result.get("Uploads"):
        for upload in items:
            if "Key" not in upload or "UploadId" not in upload:
                continue
            client.abort_multipart_upload(
                Bucket=storage.settings.bucket, Key=upload["Key"], UploadId=upload["UploadId"]
            )

    return storage


class TestSettings:
    def test_initialize(self, storage: Storage):
        """Initialize flag controls container creation."""
        storage.settings.client.delete_bucket(Bucket=storage.settings.bucket)

        params = {field.name: getattr(storage.settings, field.name) for field in dataclasses.fields(storage.settings)}

        with pytest.raises(fk.exc.InvalidStorageConfigurationError):
            storage.SettingsFactory.from_dict(dict(params, initialize=False))

        storage.SettingsFactory.from_dict(dict(params, initialize=True))
        assert storage.settings.client.head_bucket(Bucket=storage.settings.bucket)


class TestStorage(standard.Standard):
    def test_create_hash_md5(self, storage: fk.Storage, faker: Faker):
        """Storage can create an MD5 hash of the uploaded content."""
        storage.settings.hashing_algorithm = "md5"
        content = b"test content"
        result = storage.upload(fk.Location(faker.file_name()), fk.make_upload(content))
        assert result.hash == "9473fdd0d880a43c21b7778d34872157", "Uploaded file has wrong hash"

        result = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(b"0" * 1024 * 1024 * 10),
        )
        # for files bigger than 8MB the hash is calculated as a hash-of-hashes
        # and suffixed with number of parts
        assert result.hash == "37789b909bf3cd1199703964ac04e9c5-2", "Multiparted file has wrong hash"

    def test_create_hash_crc32(self, storage: fk.Storage, faker: Faker):
        """Storage can create a CRC32 hash of the uploaded content."""
        storage.settings.hashing_algorithm = "crc32"
        content = b"test content"
        result = storage.upload(fk.Location(faker.file_name()), fk.make_upload(content))
        assert result.hash == "V/RnXQ==", "Uploaded file has wrong hash"

        result = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(b"0" * 1024 * 1024 * 10),
        )
        # CRC* algorithms are computed for FULL_OBJECT
        assert result.hash == "bsz5YQ==", "Multiparted file has wrong hash"

    def test_create_hash_crc32c(self, storage: fk.Storage, faker: Faker):
        """Storage can create a CRC32C hash of the uploaded content."""
        storage.settings.hashing_algorithm = "crc32c"
        content = b"test content"
        result = storage.upload(fk.Location(faker.file_name()), fk.make_upload(content))
        assert result.hash == "GTCnBQ==", "Uploaded file has wrong hash"

        result = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(b"0" * 1024 * 1024 * 10),
        )
        # CRC* algorithms are computed for FULL_OBJECT
        assert result.hash == "uah/dg==", "Multiparted file has wrong hash"

    def test_create_hash_crc64nvme(self, storage: fk.Storage, faker: Faker):
        """Storage can create a CRC64NVME hash of the uploaded content."""
        storage.settings.hashing_algorithm = "crc64nvme"
        content = b"test content"
        result = storage.upload(fk.Location(faker.file_name()), fk.make_upload(content))
        assert result.hash == "Z9s8rhTbnlM=", "Uploaded file has wrong hash"

        result = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(b"0" * 1024 * 1024 * 10),
        )
        # CRC* algorithms are computed for FULL_OBJECT
        assert result.hash == "Naz0uXkYBPM=", "Multiparted file has wrong hash"

    def test_create_hash_sha256(self, storage: fk.Storage, faker: Faker):
        """Storage can create a SHA256 hash of the uploaded content."""
        storage.settings.hashing_algorithm = "sha256"
        content = b"test content"
        result = storage.upload(fk.Location(faker.file_name()), fk.make_upload(content))
        assert result.hash == "auinVVUgn9bEQVfArtgBbnY/9DWhnPGG92hjFAFD/3I=", "Uploaded file has wrong hash"

        result = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(b"0" * 1024 * 1024 * 10),
        )
        # SHA* algorithms are computed as COMPOSED for multipart uploads
        assert result.hash == "GF9NksQm+kfiSnfATwel6YpQv0lVjL0yZLPwkK+pBGw=-2", "Multiparted file has wrong hash"

    def test_multipart_create_hash_md5(self, storage: fk.Storage, faker: Faker):
        """Storage can create an MD5 hash of the uploaded content during multipart upload."""
        storage.settings.hashing_algorithm = "md5"

        first = fk.make_upload(b"0" * 1024 * 1024 * 5)
        second = fk.make_upload(b"0" * 1024 * 1024 * 5)
        data = storage.multipart_start(fk.Location(faker.file_name()), 1024**2 * 10)
        data = storage.multipart_update(data, first, 0)
        data = storage.multipart_update(data, second, 1)
        data = storage.multipart_complete(data)

        # multipart hash is calculated as a hash-of-hashes and suffixed with
        # number of parts
        assert data.hash == "272ada4522d2daacb1a41098a5ae8f63-2", "Multiparted file has wrong hash"

    def test_multipart_create_hash_crc64nvme(self, storage: fk.Storage, faker: Faker):
        """Storage can create a CRC64NVME hash of the uploaded content during multipart upload."""
        storage.settings.hashing_algorithm = "crc64nvme"

        first = fk.make_upload(b"0" * 1024 * 1024 * 5)
        second = fk.make_upload(b"0" * 1024 * 1024 * 5)
        data = storage.multipart_start(fk.Location(faker.file_name()), 1024**2 * 10)
        data = storage.multipart_update(data, first, 0)
        data = storage.multipart_update(data, second, 1)
        data = storage.multipart_complete(data)

        # CRC* algorithms are computed for FULL_OBJECT
        assert data.hash == "Naz0uXkYBPM=", "Multiparted file has wrong hash"

    def test_copy_hash_md5(self, storage: fk.Storage, faker: Faker):
        """Storage can create an MD5 hash of the copied content."""
        storage.settings.hashing_algorithm = "md5"

        data = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(b"0" * 1024 * 1024 * 10),
        )
        result = storage.copy(fk.Location(faker.file_name()), data)
        assert result.hash == "37789b909bf3cd1199703964ac04e9c5-2", "Copied file has wrong hash"

    def test_copy_hash_crc64nvme(self, storage: fk.Storage, faker: Faker):
        """Storage can create a CRC64NVME hash of the copied content."""
        storage.settings.hashing_algorithm = "crc64nvme"
        data = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(b"0" * 1024 * 1024 * 10),
        )
        result = storage.copy(fk.Location(faker.file_name()), data)
        assert result.hash == "Naz0uXkYBPM=", "Copied file has wrong hash"

    def test_upload_multipart_threshold(self, storage: fk.Storage, faker: Faker):
        """Test that the multipart threshold setting affects the upload operation."""
        content = b"0" * 1024 * 1024 * 10
        result = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(content),
        )
        assert result.hash == "37789b909bf3cd1199703964ac04e9c5-2", "Uploaded file has wrong hash"

        storage.settings.multipart_threshold = 20 * 1024**2  # pyright: ignore[reportAttributeAccessIssue]
        result = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(content),
        )
        assert result.hash == "0c1d32c858f2a7206e347da53b2b046d", "Uploaded file has wrong hash"

    def test_copy_multipart_threshold(self, storage: fk.Storage, faker: Faker):
        """Test that the multipart threshold setting affects the copy operation."""
        data = storage.upload(
            fk.Location(faker.file_name()),
            fk.make_upload(b"0" * 1024 * 1024 * 10),
        )
        result = storage.copy(fk.Location(faker.file_name()), data)
        assert result.hash == "37789b909bf3cd1199703964ac04e9c5-2", "Copied file has wrong hash"

        storage.settings.multipart_threshold = 20 * 1024**2  # pyright: ignore[reportAttributeAccessIssue]
        result = storage.copy(fk.Location(faker.file_name()), data)
        assert result.hash == "0c1d32c858f2a7206e347da53b2b046d", "Copied file has wrong hash"
