from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOUNDARIES = {
    "shared": {"naive", "stream", "comparison"},
    "naive": {"stream", "comparison"},
    "stream": {"naive", "comparison"},
    "comparison": {"shared", "naive", "stream"},
    "scripts": {"naive", "stream", "comparison"},
}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def production_files(package: str) -> list[Path]:
    return [
        path
        for path in (ROOT / package).rglob("*.py")
        if "tests" not in path.relative_to(ROOT / package).parts
    ]


def test_packages_obey_one_way_dependency_boundaries() -> None:
    violations: list[str] = []
    for package, forbidden in BOUNDARIES.items():
        for path in production_files(package):
            invalid = imported_roots(path) & forbidden
            if invalid:
                violations.append(f"{path.relative_to(ROOT)} imports {sorted(invalid)}")

    assert violations == []


def test_each_service_has_its_own_entrypoint() -> None:
    from naive.api import app as naive_app
    from stream.api import app as stream_app

    assert naive_app.title == "Naive RAG API"
    assert stream_app.title == "StreamRAG API"
    assert naive_app is not stream_app


def test_frontend_is_not_owned_by_the_comparison_package() -> None:
    assert (ROOT / "frontend" / "package.json").is_file()
    assert not (ROOT / "comparison" / "frontend").exists()


def test_frontend_exposes_stable_single_origin_routes() -> None:
    nginx = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    routes = (ROOT / "frontend" / "src" / "routes.ts").read_text(encoding="utf-8")

    assert "try_files $uri $uri/ /index.html;" in nginx
    assert "location /api/naive/" in nginx
    assert "server naive:8001 resolve;" in nginx
    assert "proxy_pass http://naive_backend/;" in nginx
    assert "location /api/stream/" in nginx
    assert "server stream:8002 resolve;" in nginx
    assert "proxy_pass http://stream_backend/;" in nginx
    assert 'naive: "/naive"' in routes
    assert 'stream: "/stream"' in routes
    assert 'compare: "/compare"' in routes


def _compose_service(compose: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n", compose, flags=re.MULTILINE)
    assert match is not None
    start = match.start()
    next_match = re.search(r"^  [a-z0-9-]+:\n", compose[match.end() :], flags=re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(compose)
    return compose[start:end]


def test_docker_stack_isolates_persistent_state() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    naive_api = _compose_service(compose, "naive")
    stream_api = _compose_service(compose, "stream")
    naive_qdrant = _compose_service(compose, "qdrant-naive")
    stream_qdrant = _compose_service(compose, "qdrant-stream")

    assert "QDRANT_URL: http://qdrant-naive:6333" in naive_api
    assert "QDRANT_URL: http://qdrant-stream:6333" in stream_api
    assert "QDRANT_API_KEY: \"\"" in naive_api
    assert "QDRANT_API_KEY: \"\"" in stream_api
    assert "restart: unless-stopped" in naive_api
    assert "restart: unless-stopped" in stream_api
    assert '127.0.0.1:8001/v1/health' in naive_api
    assert '127.0.0.1:8002/v1/health' in stream_api
    assert ".get('ok')" in naive_api
    assert ".get('ok')" in stream_api
    assert "naive-runtime:/app/var" in naive_api
    assert "stream-runtime:/app/var" in stream_api
    assert "networks:\n      - edge\n      - naive-data" in naive_api
    assert "networks:\n      - edge\n      - stream-data" in stream_api
    assert "stream-data" not in naive_api
    assert "naive-data" not in stream_api

    image = "qdrant/qdrant:v1.18.3-unprivileged@sha256:"
    assert image in naive_qdrant
    assert image in stream_qdrant
    assert 'QDRANT__TELEMETRY_DISABLED: "true"' in naive_qdrant
    assert 'QDRANT__TELEMETRY_DISABLED: "true"' in stream_qdrant
    assert "naive-qdrant:/qdrant/storage" in naive_qdrant
    assert "stream-qdrant:/qdrant/storage" in stream_qdrant
    assert "networks:\n      - naive-data" in naive_qdrant
    assert "networks:\n      - stream-data" in stream_qdrant
    assert "ports:" not in naive_qdrant
    assert "ports:" not in stream_qdrant
    assert "stream-data" not in naive_qdrant
    assert "naive-data" not in stream_qdrant
    assert "6333:6333" not in compose
    assert (
        "networks:\n"
        "  edge:\n"
        "  naive-data:\n"
        "    internal: true\n"
        "  stream-data:\n"
        "    internal: true"
    ) in compose

    frontend = _compose_service(compose, "frontend")
    assert "restart: unless-stopped" in frontend
    assert "http://127.0.0.1/healthz" in frontend
    assert "condition: service_healthy" in frontend
    assert "networks:\n      - edge" in frontend


def test_app_stack_does_not_depend_on_comparison_state() -> None:
    script = (ROOT / "scripts" / "dev_stack.sh").read_text(encoding="utf-8")

    assert "APP_STATE_ROOT" in script
    assert "comparison/" not in script


def run_with_blocked_imports(blocked: set[str], source: str) -> subprocess.CompletedProcess[str]:
    prelude = f"""
        import importlib.abc
        import sys

        blocked = {sorted(blocked)!r}

        class BlockedImportFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.partition(".")[0] in blocked:
                    raise ImportError(f"blocked optional package: {{fullname}}")
                return None

        sys.meta_path.insert(0, BlockedImportFinder())
    """
    program = f"{textwrap.dedent(prelude)}\n{textwrap.dedent(source)}"
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def assert_isolated_import_succeeds(blocked: set[str], source: str) -> None:
    result = run_with_blocked_imports(blocked, source)
    assert result.returncode == 0, result.stderr


def test_naive_entrypoint_imports_without_stream_or_comparison() -> None:
    assert_isolated_import_succeeds(
        {"stream", "comparison"},
        """
        from naive.api import app

        routes = {getattr(route, "path", "") for route in app.routes}
        assert app.title == "Naive RAG API"
        assert "/v1/turns/{turn_id}/snapshots" not in routes
        """,
    )


def test_stream_entrypoint_imports_without_naive_or_comparison() -> None:
    assert_isolated_import_succeeds(
        {"naive", "comparison"},
        """
        from stream.api import app

        routes = {getattr(route, "path", "") for route in app.routes}
        assert app.title == "StreamRAG API"
        assert "/v1/turns/{turn_id}/snapshots" in routes
        """,
    )


def test_shared_dataset_tooling_imports_without_any_application_package() -> None:
    assert_isolated_import_succeeds(
        {"naive", "stream", "comparison"},
        """
        from scripts.prepare_crag_text_global import heuristic_stabilization_class

        label, _ = heuristic_stabilization_class("which album was released first", "simple")
        assert label in {"early_stabilization", "late_stabilization", "revision_or_ambiguity"}
        """,
    )
