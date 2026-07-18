from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from app.agent.service import GroundedAgent
from app.api.runtime import (
    IndexMaintenanceError,
    RagRuntime,
    TurnClosedError,
    TurnConflictError,
)
from app.api.schemas import CommitAccepted, CommitRequest, SnapshotAccepted, SnapshotRequest
from app.config import settings
from app.data.crag import (
    chunk_documents,
    dataset_review_status,
    deduplicate_documents,
    load_documents,
    require_dataset_approval,
    verify_dataset_checksums,
)
from app.data.index_state import IndexStateRepository
from app.data.session_store import SessionStore
from app.data.vector_store import QdrantVectorStore
from app.fingerprints import (
    INDEX_PIPELINE_VERSION,
    index_source_sha256,
    runtime_fingerprints,
)
from app.metrics import JsonlMetricLogger
from app.rag.embeddings import OpenAIEmbedder
from app.stream.trigger import ModelTrigger


@asynccontextmanager
async def lifespan(app: FastAPI):
    sessions = SessionStore(settings.runtime_db)
    await sessions.setup()
    store = QdrantVectorStore(
        settings,
        OpenAIEmbedder(
            settings.embedding_model,
            timeout_s=settings.openai_embedding_timeout_s,
            max_retries=settings.openai_embedding_max_retries,
        ),
        IndexStateRepository(settings.runtime_db),
    )
    await store.setup()
    runtime = RagRuntime(
        settings=settings,
        store=store,
        agent=GroundedAgent(settings, store, sessions),
        trigger=ModelTrigger(settings),
        logger=JsonlMetricLogger(settings.metrics_log),
    )
    app.state.runtime = runtime
    runtime.start_maintenance()
    yield
    await runtime.shutdown()


app = FastAPI(
    title="Typed StreamRAG Assessment API",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Last-Event-ID"],
)


@app.get("/")
async def root() -> dict:
    return {"service": "typed-streamrag-api", "docs": "/docs", "health": "/v1/health"}


@app.get("/v1/health")
async def health() -> dict:
    runtime: RagRuntime = app.state.runtime
    collection = await runtime.store.get_collection()
    index_metadata = await runtime.store.state.metadata(settings.qdrant_collection)
    current_index_source, checksums_valid, approval_status = await asyncio.to_thread(
        _dataset_health_state
    )
    indexed_chunks = int(collection.points_count or 0)
    desired_chunks = int(index_metadata["desired_chunks"] or 0)
    index_matches = (
        index_metadata["index_source_sha256"] == current_index_source
        and indexed_chunks == desired_chunks
        and desired_chunks > 0
    )
    return {
        "ok": checksums_valid and index_matches,
        "index_ready": checksums_valid and index_matches,
        "dataset_status": approval_status,
        "collection": settings.qdrant_collection,
        "indexed_chunks": indexed_chunks,
        "indexed_desired_chunks": desired_chunks,
        "dataset_checksums_valid": checksums_valid,
        "index_matches_current_corpus": index_matches,
        "model": settings.openai_model,
        "embedding_model": settings.embedding_model,
        "reasoning_effort": settings.reasoning_effort,
        "trigger_reasoning_effort": settings.trigger_reasoning_effort,
        "summary_reasoning_effort": settings.summary_reasoning_effort,
        "service_tier": settings.openai_service_tier,
        "instance_id": runtime.instance_id,
    }


@app.get("/v1/data/status")
async def data_status() -> dict:
    runtime: RagRuntime = app.state.runtime
    collection = await runtime.store.get_collection()
    index_metadata = await runtime.store.state.metadata(settings.qdrant_collection)
    dataset_state = await asyncio.to_thread(_dataset_status_state)
    return {
        "approval_status": dataset_state["approval_status"],
        "indexed_chunks": int(collection.points_count or 0),
        "index_version": index_metadata["version"],
        "index_checksum": index_metadata["index_checksum"],
        "indexed_desired_chunks": index_metadata["desired_chunks"],
        "dataset_checksums_valid": dataset_state["checksums_valid"],
        "dataset_checksum_error": dataset_state["checksum_error"],
        "dataset_verified_files": len(dataset_state["verified_files"]),
        "index_source_sha256": index_metadata["index_source_sha256"],
        "current_index_source_sha256": dataset_state["current_index_source"],
        "index_matches_current_corpus": (
            index_metadata["index_source_sha256"] == dataset_state["current_index_source"]
        ),
        "instance_id": runtime.instance_id,
        "model": settings.openai_model,
        "embedding_model": settings.embedding_model,
        "reasoning_effort": settings.reasoning_effort,
        "trigger_reasoning_effort": settings.trigger_reasoning_effort,
        "summary_reasoning_effort": settings.summary_reasoning_effort,
        "service_tier": settings.openai_service_tier,
        "index_pipeline_version": INDEX_PIPELINE_VERSION,
        **dataset_state["fingerprints"],
    }


def _dataset_health_state() -> tuple[str, bool, str]:
    current_index_source = index_source_sha256(settings)
    try:
        verify_dataset_checksums(settings.dataset_dir)
        checksums_valid = True
    except RuntimeError:
        checksums_valid = False
    return current_index_source, checksums_valid, dataset_review_status(settings.dataset_dir)


def _dataset_status_state() -> dict:
    try:
        verified_files = verify_dataset_checksums(settings.dataset_dir)
        checksums_valid = True
        checksum_error = None
    except RuntimeError as exc:
        verified_files = {}
        checksums_valid = False
        checksum_error = str(exc)
    return {
        "approval_status": dataset_review_status(settings.dataset_dir),
        "checksums_valid": checksums_valid,
        "checksum_error": checksum_error,
        "verified_files": verified_files,
        "current_index_source": index_source_sha256(settings),
        "fingerprints": runtime_fingerprints(settings),
    }


@app.post("/v1/data/sync")
async def sync_data() -> dict:
    try:
        approval = await asyncio.to_thread(
            require_dataset_approval,
            settings.dataset_dir,
            settings.allow_unreviewed_dataset,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    runtime: RagRuntime = app.state.runtime
    if runtime.index_maintenance.locked() or runtime.tasks or runtime.turns:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="index sync requires an idle service",
        )
    async with runtime.index_maintenance:
        if runtime.tasks or runtime.turns:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="index sync requires an idle service",
            )
        chunks = await asyncio.to_thread(_load_chunks)
        report = await runtime.store.sync(chunks)
    return {**report.__dict__, "dataset_status": approval}


def _load_chunks():
    documents = deduplicate_documents(load_documents(settings.dataset_dir))
    return chunk_documents(documents, settings.chunk_tokens, settings.chunk_overlap)


@app.post(
    "/v1/turns/{turn_id}/snapshots",
    response_model=SnapshotAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def snapshot(turn_id: str, payload: SnapshotRequest) -> SnapshotAccepted:
    try:
        await app.state.runtime.accept_snapshot(turn_id, payload)
    except (TurnClosedError, TurnConflictError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IndexMaintenanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return SnapshotAccepted(
        turn_id=turn_id,
        revision=payload.revision,
        events_url=f"/v1/turns/{turn_id}/events",
    )


@app.get("/v1/turns/{turn_id}/events")
async def turn_events(
    turn_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None),
) -> EventSourceResponse:
    channel = await app.state.runtime.events.existing(f"turn:{turn_id}")
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown turn")
    after = int(last_event_id or 0)
    return EventSourceResponse(channel.subscribe(after), ping=15)


@app.delete("/v1/turns/{turn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_turn(turn_id: str) -> None:
    await app.state.runtime.cancel_turn(turn_id)


@app.post(
    "/v1/turns/{turn_id}/commit",
    response_model=CommitAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def commit(turn_id: str, payload: CommitRequest) -> CommitAccepted:
    try:
        run_id = await app.state.runtime.start_commit(turn_id, payload)
    except (TurnClosedError, TurnConflictError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IndexMaintenanceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return CommitAccepted(
        run_id=run_id,
        turn_id=turn_id,
        path=payload.path,
        events_url=f"/v1/runs/{run_id}/events",
    )


@app.get("/v1/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None),
) -> EventSourceResponse:
    channel = await app.state.runtime.events.existing(f"run:{run_id}")
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown run")
    after = int(last_event_id or 0)
    return EventSourceResponse(channel.subscribe(after), ping=15)


@app.get("/v1/ops/metrics")
async def ops_metrics() -> dict:
    runtime: RagRuntime = app.state.runtime
    return {
        **runtime.counters.__dict__,
        "active_runs": len(runtime.tasks),
        "active_stream_turns": len(runtime.turns),
    }
