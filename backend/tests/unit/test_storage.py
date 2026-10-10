import os

import pytest

from data_platform.errors import InsufficientDiskError, PermanentError
from data_platform.storage import LocalArtifactStorage, sha256_file, validate_key


@pytest.fixture
def storage(tmp_path):
    return LocalArtifactStorage(tmp_path / "store")


@pytest.mark.parametrize("key", ["", "/abs", "a/../b", "a//b", "a\\b", ".."])
def test_rejects_unsafe_keys(key):
    with pytest.raises(PermanentError):
        validate_key(key)


def test_put_file_copies_and_never_overwrites(storage, tmp_path):
    source = tmp_path / "file.bin"
    source.write_bytes(b"hello")
    stored = storage.put_file(source, "bronze/x/file.bin")
    assert stored.size_bytes == 5 and storage.exists("bronze/x/file.bin")
    with pytest.raises(PermanentError):
        storage.put_file(source, "bronze/x/file.bin")


def test_put_file_hard_links_on_same_filesystem(storage, tmp_path):
    source = tmp_path / "big.pbf"
    source.write_bytes(b"x" * 1000)
    storage.put_file(source, "bronze/osm/a.pbf", link=True)
    assert os.stat(source).st_ino == os.stat(storage.local_path("bronze/osm/a.pbf")).st_ino


def test_open_write_is_atomic(storage):
    with pytest.raises(RuntimeError):
        with storage.open_write("a/partial.bin") as handle:
            handle.write(b"half")
            raise RuntimeError("network died")
    assert not storage.exists("a/partial.bin")
    assert storage.list("a") == []
    with storage.open_write("a/full.bin") as handle:
        handle.write(b"complete")
    assert storage.size("a/full.bin") == 8


def test_staging_directory_commit(storage):
    key, path = storage.staging_directory()
    (path / "part-0.parquet").write_bytes(b"data")
    storage.commit_directory(key, "silver/places/v=1")
    assert storage.exists("silver/places/v=1/part-0.parquet")
    assert not storage.exists(key)


def test_disk_guard(storage):
    with pytest.raises(InsufficientDiskError):
        storage.ensure_free_space(storage.free_bytes() + 1, 0)


def test_sha256(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(b"abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
