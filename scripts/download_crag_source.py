#!/usr/bin/env python3
"""Download the pinned official CRAG source with fail-closed checksum validation."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import httpx

from scripts.crag_source import SOURCE_SHA256, SOURCE_URL

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "raw" / "crag_official" / "crag_task_1_and_2_dev_v5.jsonl.bz2"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(output: Path, *, url: str = SOURCE_URL, expected_sha256: str = SOURCE_SHA256) -> str:
    output = output.resolve()
    if output.is_file():
        digest = sha256_file(output)
        if digest != expected_sha256:
            raise RuntimeError(f"existing source checksum mismatch: {output}")
        return "already_present_verified"
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.partial")
    if partial.exists():
        raise RuntimeError(f"partial download already exists; inspect or remove it: {partial}")
    digest = hashlib.sha256()
    try:
        with (
            httpx.stream(
                "GET",
                url,
                follow_redirects=True,
                timeout=httpx.Timeout(60.0, read=120.0),
            ) as response,
            partial.open("xb") as handle,
        ):
            response.raise_for_status()
            for block in response.iter_bytes(1024 * 1024):
                digest.update(block)
                handle.write(block)
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise RuntimeError(
                f"downloaded source checksum mismatch: expected {expected_sha256}, got {actual}"
            )
        partial.replace(output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return "downloaded_verified"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        status = download(args.output)
    except (RuntimeError, OSError, httpx.HTTPError) as exc:
        raise SystemExit(f"CRAG source download failed: {exc}") from exc
    print(f"{status}: {args.output.resolve()}")


if __name__ == "__main__":
    main()
