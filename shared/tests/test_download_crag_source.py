from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

import scripts.download_crag_source as downloader
from scripts.download_crag_source import download

ROOT = Path(__file__).resolve().parents[2]


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, _chunk_size: int):
        yield self.payload[:3]
        yield self.payload[3:]


def test_existing_source_is_reused_only_when_checksum_matches(tmp_path) -> None:
    source = tmp_path / "source.jsonl.bz2"
    source.write_bytes(b"pinned bytes")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    assert download(source, url="https://invalid.example", expected_sha256=expected) == (
        "already_present_verified"
    )

    with pytest.raises(RuntimeError, match="existing source checksum mismatch"):
        download(source, url="https://invalid.example", expected_sha256="0" * 64)


def test_download_streams_to_verified_atomic_output(tmp_path, monkeypatch) -> None:
    payload = b"official compressed source"
    output = tmp_path / "raw" / "source.jsonl.bz2"
    expected = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(downloader.httpx, "stream", lambda *_args, **_kwargs: FakeResponse(payload))

    assert download(output, url="https://example.test/source", expected_sha256=expected) == (
        "downloaded_verified"
    )
    assert output.read_bytes() == payload
    assert not output.with_name(f".{output.name}.partial").exists()


def test_checksum_failure_removes_partial_download(tmp_path, monkeypatch) -> None:
    output = tmp_path / "source.jsonl.bz2"
    monkeypatch.setattr(
        downloader.httpx,
        "stream",
        lambda *_args, **_kwargs: FakeResponse(b"unexpected bytes"),
    )

    with pytest.raises(RuntimeError, match="downloaded source checksum mismatch"):
        download(output, url="https://example.test/source", expected_sha256="0" * 64)

    assert not output.exists()
    assert not output.with_name(f".{output.name}.partial").exists()


def test_rebuild_target_stages_and_verifies_outside_committed_dataset() -> None:
    result = subprocess.run(
        ["make", "-n", "rebuild-dataset"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "scripts.download_crag_source --output data/raw/crag_official/" in result.stdout
    assert "scripts.prepare_crag_text_global" in result.stdout
    assert "--output-dir var/rebuilt-crag-eval" in result.stdout
    assert "scripts.verify_dataset --dataset-dir var/rebuilt-crag-eval" in result.stdout
    assert "--output-dir data/crag_eval" not in result.stdout
