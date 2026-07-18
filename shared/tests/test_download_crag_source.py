from __future__ import annotations

import hashlib

import pytest

from scripts.download_crag_source import download


def test_existing_source_is_reused_only_when_checksum_matches(tmp_path) -> None:
    source = tmp_path / "source.jsonl.bz2"
    source.write_bytes(b"pinned bytes")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    assert download(source, url="https://invalid.example", expected_sha256=expected) == (
        "already_present_verified"
    )

    with pytest.raises(RuntimeError, match="existing source checksum mismatch"):
        download(source, url="https://invalid.example", expected_sha256="0" * 64)
