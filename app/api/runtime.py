from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field

from app.agent.context import evidence_block
from app.agent.service import GroundedAgent
from app.api.events import EventRegistry
from app.api.schemas import CommitRequest, SnapshotRequest
from app.config import Settings
from app.data.crag import require_dataset_snapshot
from app.data.vector_store import IndexNotReadyError, QdrantVectorStore
from app.fingerprints import index_source_sha256_for_documents, runtime_fingerprints
from app.metrics import JsonlMetricLogger, model_cost
from app.models import InputSnapshot, SearchResult, Usage
from app.stream.coordinator import StreamCoordinator
from app.stream.snapshot import SnapshotAnalyzer
from app.stream.trigger import ModelTrigger, bounded_retrieval_query

TERMINAL_TURN_LIMIT = 4096
TURN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
logger = logging.getLogger(__name__)


@dataclass
class RuntimeCounters:
    runs_started: int = 0
    path_completions: int = 0
    path_failures: int = 0


class TurnClosedError(RuntimeError):
    """A snapshot arrived after the immutable commit/cancel boundary."""


class TurnConflictError(RuntimeError):
    """A turn ID was reused with a different session, path, or commit payload."""


class IndexMaintenanceError(RuntimeError):
    """Live turn admission is unavailable while the index is being updated."""


@dataclass
class CommitReservation:
    run_id: str
    payload_sha256: str
    session_id: str
    path: str
    started: bool = False
    setup_error: str | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class PathTelemetry:
    result: SearchResult
    retrieval_started_ms: float
    retrieval_ready_ms: float
    controller_usage: Usage = field(default_factory=Usage)
    controller_calls: int = 0
    controller_timeouts: int = 0
    controller_failures: int = 0
    controller_elapsed_ms: float = 0.0
    retrieval_calls: int = 1
    raw_retrieval_calls: int = 0
    settled_draft_retrievals: int = 0
    retrieval_embedding_tokens: int = 0
    retrieval_query_vector_ms: float = 0.0
    retrieval_ann_ms: float = 0.0
    retrieval_timeouts: int = 0
    retrieval_failures: int = 0
    stale_discards: int = 0
    trigger_cancellations: int = 0
    unpriced_trigger_cancellations: int = 0
    retrieval_cancellations: int = 0
    evidence_reuses: int = 0
    evidence_revalidations: int = 0
    commit_fallbacks: int = 0
    first_speculative_started_ms: float | None = None
    first_speculative_ready_ms: float | None = None
    accepted_revision: int | None = None
    accepted_from_fallback: bool | None = None
    accepted_ready_before_commit: bool | None = None
    accepted_speculative_completed_ms: float | None = None
    commit_gate_ms: float = 0.0


@dataclass
class CommittedTextRetrieval:
    """Exact complete-input retrieval result used by Naive RAG."""

    result: SearchResult
    started_ms: float
    completed_ms: float


class RagRuntime:
    def __init__(
        self,
        *,
        settings: Settings,
        store: QdrantVectorStore,
        agent: GroundedAgent,
        trigger: ModelTrigger,
        logger: JsonlMetricLogger,
    ):
        self.settings = settings
        self.store = store
        self.agent = agent
        self.trigger = trigger
        self.logger = logger
        self.events = EventRegistry()
        self.turns: dict[str, StreamCoordinator] = {}
        self.turn_bindings: dict[str, tuple[str, str]] = {}
        self.terminal_turns: dict[str, CommitReservation | None] = {}
        self.terminal_turn_limit = TERMINAL_TURN_LIMIT
        self.tasks: set[asyncio.Task] = set()
        self.turn_tasks: dict[str, set[asyncio.Task]] = {}
        self.maintenance_tasks: set[asyncio.Task] = set()
        self.counters = RuntimeCounters()
        self._turn_lock = asyncio.Lock()
        self.index_maintenance = asyncio.Lock()
        self._index_maintenance_admitted = False
        self.instance_id = str(uuid.uuid4())
        self.fingerprints = runtime_fingerprints(settings)

    def _ensure_index_available_locked(self) -> None:
        if self._index_maintenance_admitted or self.index_maintenance.locked():
            raise IndexMaintenanceError("index maintenance is in progress")

    @staticmethod
    def _validate_turn_id(turn_id: str) -> None:
        if TURN_ID_PATTERN.fullmatch(turn_id) is None:
            raise ValueError("turn_id must be 1-128 URL-safe letters, digits, or ._:- characters")

    async def _assert_index_ready(self) -> None:
        try:
            snapshot = await asyncio.to_thread(
                require_dataset_snapshot,
                self.settings.dataset_dir,
                self.settings.allow_unreviewed_dataset,
            )
            expected_source = index_source_sha256_for_documents(
                self.settings,
                snapshot.documents_sha256,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise IndexNotReadyError(
                "current dataset is not approved and checksum-valid; repair it and run data sync"
            ) from exc
        await self.store.assert_ready(expected_source)

    def _has_pending_commit_setup_locked(self) -> bool:
        return any(
            reservation is not None and not reservation.started
            for reservation in self.terminal_turns.values()
        )

    @asynccontextmanager
    async def index_maintenance_guard(self) -> AsyncIterator[None]:
        """Claim exclusive index maintenance only while the runtime is idle."""
        async with self._turn_lock:
            self._ensure_index_available_locked()
            if self.tasks or self.turns or self._has_pending_commit_setup_locked():
                raise IndexMaintenanceError("index sync requires an idle service")
            self._index_maintenance_admitted = True
        try:
            async with self.index_maintenance:
                yield
        finally:
            async with self._turn_lock:
                self._index_maintenance_admitted = False

    def start_maintenance(self) -> None:
        task = asyncio.create_task(self._maintenance_loop())
        self.maintenance_tasks.add(task)
        task.add_done_callback(self._maintenance_done)

    def _maintenance_done(self, task: asyncio.Task) -> None:
        self.maintenance_tasks.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is None:
            logger.error("runtime maintenance loop exited unexpectedly")
            return
        logger.error(
            "runtime maintenance loop crashed",
            exc_info=(type(exception), exception, exception.__traceback__),
        )

    async def _maintenance_cycle(self) -> None:
        try:
            await self.reap_idle_turns()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("idle-turn maintenance failed; the loop will continue")
        try:
            await self.agent.sessions.prune(self.settings.session_retention_hours)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("session-pruning maintenance failed; the loop will continue")

    async def _maintenance_loop(self) -> None:
        interval = max(1.0, min(30.0, self.settings.turn_idle_timeout_s / 2))
        while True:
            await asyncio.sleep(interval)
            await self._maintenance_cycle()

    async def reap_idle_turns(self, now_ms: float | None = None) -> int:
        now_ms = now_ms or time.perf_counter() * 1000
        cutoff_ms = now_ms - self.settings.turn_idle_timeout_s * 1000
        async with self._turn_lock:
            expired = [
                (turn_id, coordinator)
                for turn_id, coordinator in self.turns.items()
                if not coordinator.committed and coordinator.last_activity_ms < cutoff_ms
            ]
        reaped = 0
        for turn_id, coordinator in expired:
            reaped += await self._cancel_idle_turn(turn_id, coordinator, cutoff_ms)
        return reaped

    async def _cancel_idle_turn(
        self,
        turn_id: str,
        expected: StreamCoordinator,
        cutoff_ms: float,
    ) -> int:
        """Cancel only the same still-uncommitted coordinator selected by the reaper."""
        async with self._turn_lock:
            coordinator = self.turns.get(turn_id)
            if (
                coordinator is not expected
                or coordinator.committed
                or coordinator.last_activity_ms >= cutoff_ms
                or turn_id in self.terminal_turns
                or turn_id in self.turn_tasks
            ):
                return 0
            self.terminal_turns[turn_id] = None
            self._prune_terminal_turns_locked(protected_turn_id=turn_id)
            self.turns.pop(turn_id, None)
            self.turn_bindings.pop(turn_id, None)
        await coordinator.close()
        await self.events.close_and_remove(f"turn:{turn_id}")
        return 1

    @staticmethod
    def _cache_scope(session_id: str, path: str) -> str:
        """Keep every implementation's retrieval work causally isolated."""
        return f"{session_id}:{path}"

    async def _retrieve_committed_text(
        self,
        snapshot: InputSnapshot,
        *,
        session_id: str,
        path: str,
    ) -> CommittedTextRetrieval:
        """Retrieve the immutable committed text once for the Naive baseline."""
        query = bounded_retrieval_query(snapshot.text)
        started_ms = time.perf_counter() * 1000
        try:
            result = await asyncio.wait_for(
                self.store.search(
                    query,
                    cache_scope=self._cache_scope(session_id, path),
                ),
                timeout=self.settings.retrieval_timeout_s,
            )
        except TimeoutError:
            raise RuntimeError("committed-text retrieval timed out") from None
        completed_ms = time.perf_counter() * 1000
        return CommittedTextRetrieval(
            result=result,
            started_ms=started_ms,
            completed_ms=completed_ms,
        )

    async def _coordinator(
        self,
        turn_id: str,
        *,
        session_id: str,
        request_path: str,
        allow_terminal: bool = False,
    ) -> StreamCoordinator:
        # Do the potentially waiting SQLite/context reads outside the global
        # turn registry lock.  A second locked check below preserves the
        # single-coordinator invariant if two first snapshots race.
        async with self._turn_lock:
            self._ensure_index_available_locked()
            if turn_id in self.terminal_turns and not allow_terminal:
                raise TurnClosedError(f"turn {turn_id!r} is already committed or cancelled")
            existing = self.turns.get(turn_id)
            if existing:
                if self.turn_bindings.get(turn_id) != (session_id, request_path):
                    raise TurnConflictError(
                        "turn_id is already bound to a different session or path"
                    )
                return existing

        await self._assert_index_ready()
        conversation_context = await self.agent.conversation_context(f"{session_id}:stream")

        async with self._turn_lock:
            self._ensure_index_available_locked()
            if turn_id in self.terminal_turns and not allow_terminal:
                raise TurnClosedError(f"turn {turn_id!r} is already committed or cancelled")
            existing = self.turns.get(turn_id)
            if existing:
                if self.turn_bindings.get(turn_id) != (session_id, request_path):
                    raise TurnConflictError(
                        "turn_id is already bound to a different session or path"
                    )
                return existing

            async def publish(payload: dict) -> None:
                channel = await self.events.get(f"turn:{turn_id}")
                await channel.publish(payload)

            coordinator = StreamCoordinator(
                turn_id=turn_id,
                index=self.store,
                trigger=self.trigger,
                settings=self.settings,
                send=publish,
                conversation_context=conversation_context,
                analyzer=SnapshotAnalyzer(),
                cache_scope=self._cache_scope(session_id or turn_id, "stream"),
            )
            self.turns[turn_id] = coordinator
            self.turn_bindings[turn_id] = (session_id, request_path)
            return coordinator

    async def accept_snapshot(self, turn_id: str, request: SnapshotRequest) -> None:
        self._validate_turn_id(turn_id)
        coordinator = await self._coordinator(
            turn_id,
            session_id=request.session_id,
            request_path=request.path,
        )
        await coordinator.update(
            InputSnapshot(
                turn_id=turn_id,
                revision=request.revision,
                text=request.text,
            )
        )

    async def start_commit(self, turn_id: str, request: CommitRequest) -> str:
        self._validate_turn_id(turn_id)
        committed_ms = time.perf_counter() * 1000
        payload_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "path": request.path,
                    "query_time": request.query_time,
                    "revision": request.revision,
                    "session_id": request.session_id,
                    "text": request.text,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        run_id = str(uuid.uuid4())
        reservation = CommitReservation(
            run_id=run_id,
            payload_sha256=payload_sha256,
            session_id=request.session_id,
            path=request.path,
        )
        pending_duplicate: CommitReservation | None = None
        async with self._turn_lock:
            if turn_id in self.terminal_turns:
                existing = self.terminal_turns[turn_id]
                if existing is not None and existing.payload_sha256 == payload_sha256:
                    if existing.started:
                        return existing.run_id
                    pending_duplicate = existing
                else:
                    raise TurnConflictError(
                        "turn_id already reached a different commit or cancel boundary"
                    )
            else:
                self._ensure_index_available_locked()
                binding = self.turn_bindings.get(turn_id)
                if binding is not None and binding != (request.session_id, request.path):
                    raise TurnConflictError(
                        "turn_id is already bound to a different session or path"
                    )
                # Reserve the immutable Send boundary before any readiness or
                # context await. The idle reaper and index maintenance gate
                # must observe an in-progress commit from this point onward.
                self.terminal_turns[turn_id] = reservation
                self._prune_terminal_turns_locked(protected_turn_id=turn_id)
        if pending_duplicate is not None:
            await pending_duplicate.ready.wait()
            if pending_duplicate.started:
                return pending_duplicate.run_id
            raise RuntimeError(
                "the original commit setup failed; retry the commit request"
                + (f": {pending_duplicate.setup_error}" if pending_duplicate.setup_error else "")
            )
        try:
            await self._assert_index_ready()
            coordinator: StreamCoordinator | None = None
            if request.path in {"stream", "compare"}:
                coordinator = await self._coordinator(
                    turn_id,
                    session_id=request.session_id,
                    request_path=request.path,
                    allow_terminal=True,
                )
            else:
                async with self._turn_lock:
                    speculative = self.turns.pop(turn_id, None)
                    self.turn_bindings.pop(turn_id, None)
                if speculative is not None:
                    await speculative.close()
            await self.events.get(f"run:{run_id}")
            async with self._turn_lock:
                self._ensure_index_available_locked()
                if self.terminal_turns.get(turn_id) is not reservation:
                    raise TurnClosedError("turn was cancelled during commit setup")
                if coordinator is not None:
                    coordinator.freeze_at_commit(
                        InputSnapshot(
                            turn_id=turn_id,
                            revision=request.revision,
                            text=request.text,
                        ),
                        committed_ms,
                    )
                self.counters.runs_started += 1
                task = asyncio.create_task(
                    self._execute(run_id, turn_id, request, committed_ms=committed_ms)
                )
                self.tasks.add(task)
                self.turn_tasks.setdefault(turn_id, set()).add(task)
                reservation.started = True
                reservation.ready.set()
                self._prune_terminal_turns_locked(protected_turn_id=turn_id)
        except BaseException as exc:
            orphaned_coordinator: StreamCoordinator | None = None
            async with self._turn_lock:
                if self.terminal_turns.get(turn_id) is reservation:
                    self.terminal_turns.pop(turn_id, None)
                # Setup may have created a coordinator after a concurrent
                # cancellation.  Remove it here so the rolled-back turn can be
                # retried cleanly and no speculative worker remains resident.
                orphaned_coordinator = self.turns.pop(turn_id, None)
                self.turn_bindings.pop(turn_id, None)
            if orphaned_coordinator is not None:
                await orphaned_coordinator.close()
            await self.events.close_and_remove(f"run:{run_id}")
            reservation.setup_error = f"{type(exc).__name__}: {exc}"
            reservation.ready.set()
            raise

        def release(completed: asyncio.Task) -> None:
            self.tasks.discard(completed)
            turn_set = self.turn_tasks.get(turn_id)
            if turn_set is not None:
                turn_set.discard(completed)
                if not turn_set:
                    self.turn_tasks.pop(turn_id, None)
            if len(self.terminal_turns) > self.terminal_turn_limit:
                prune_task = asyncio.create_task(self._prune_terminal_turns())
                self.maintenance_tasks.add(prune_task)
                prune_task.add_done_callback(self.maintenance_tasks.discard)

        task.add_done_callback(release)
        return run_id

    async def _execute(
        self,
        run_id: str,
        turn_id: str,
        request: CommitRequest,
        *,
        committed_ms: float,
    ) -> None:
        channel = await self.events.get(f"run:{run_id}")
        request_started_ms = committed_ms
        await channel.publish(
            {
                "type": "run.started",
                "run_id": run_id,
                "turn_id": turn_id,
                "requested_path": request.path,
                "execution_mode": (
                    "concurrent_ui_demo" if request.path == "compare" else "isolated_path"
                ),
            }
        )
        try:
            if request.path == "compare":
                # Both demonstrations observe the same real Send boundary. Their
                # caches and memory are isolated, but provider concurrency makes
                # this UI view illustrative rather than the reportable benchmark.
                results = await asyncio.gather(
                    self._run_naive(
                        run_id,
                        turn_id,
                        request,
                        request_started_ms,
                        request_started_ms,
                        channel.publish,
                    ),
                    self._run_stream(
                        run_id,
                        turn_id,
                        request,
                        request_started_ms,
                        request_started_ms,
                        channel.publish,
                    ),
                    return_exceptions=True,
                )
                for path, result in zip(("naive", "stream"), results, strict=True):
                    if isinstance(result, BaseException):
                        self.counters.path_failures += 1
                        await channel.publish(
                            {
                                "type": "answer.error",
                                "run_id": run_id,
                                "turn_id": turn_id,
                                "path": path,
                                "error": type(result).__name__,
                                "message": str(result),
                            }
                        )
            elif request.path == "naive":
                await self._run_naive(
                    run_id,
                    turn_id,
                    request,
                    request_started_ms,
                    request_started_ms,
                    channel.publish,
                )
            else:
                await self._run_stream(
                    run_id,
                    turn_id,
                    request,
                    request_started_ms,
                    request_started_ms,
                    channel.publish,
                )
            await channel.publish({"type": "run.completed", "run_id": run_id})
        except Exception as exc:
            self.counters.path_failures += 1
            await channel.publish(
                {
                    "type": "run.error",
                    "run_id": run_id,
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )
        finally:
            async with self._turn_lock:
                coordinator = self.turns.pop(turn_id, None)
                self.turn_bindings.pop(turn_id, None)
            if coordinator:
                await coordinator.close()
            turn_channel = await self.events.get(f"turn:{turn_id}")
            await turn_channel.close()
            await channel.close()
            self._schedule_channel_cleanup(f"turn:{turn_id}")
            self._schedule_channel_cleanup(f"run:{run_id}")

    async def _run_naive(
        self,
        run_id,
        turn_id,
        request,
        committed_ms,
        request_started_ms,
        send,
    ) -> None:
        committed_retrieval = await self._retrieve_committed_text(
            InputSnapshot(
                turn_id=turn_id,
                revision=request.revision,
                text=request.text,
            ),
            session_id=request.session_id,
            path="naive",
        )
        await self._answer(
            run_id=run_id,
            turn_id=turn_id,
            path="naive",
            request=request,
            retrieval=PathTelemetry(
                result=committed_retrieval.result,
                retrieval_started_ms=committed_retrieval.started_ms,
                retrieval_ready_ms=committed_retrieval.completed_ms,
                retrieval_calls=1,
                retrieval_embedding_tokens=committed_retrieval.result.embedding_tokens,
                retrieval_query_vector_ms=committed_retrieval.result.query_vector_ms,
                retrieval_ann_ms=committed_retrieval.result.ann_ms,
                accepted_revision=request.revision,
                accepted_from_fallback=True,
                accepted_ready_before_commit=False,
            ),
            committed_ms=committed_ms,
            request_started_ms=request_started_ms,
            send=send,
        )

    async def _run_stream(
        self,
        run_id,
        turn_id,
        request,
        committed_ms,
        request_started_ms,
        send,
    ) -> None:
        coordinator = await self._coordinator(
            turn_id,
            session_id=request.session_id,
            request_path=request.path,
            allow_terminal=True,
        )
        commit_gate_started_ms = time.perf_counter() * 1000
        evidence = await coordinator.commit(
            InputSnapshot(
                turn_id=turn_id,
                revision=request.revision,
                text=request.text,
            )
        )
        commit_gate_ms = time.perf_counter() * 1000 - commit_gate_started_ms
        metrics = coordinator.metrics
        await self._answer(
            run_id=run_id,
            turn_id=turn_id,
            path="stream",
            request=request,
            retrieval=PathTelemetry(
                result=evidence.result,
                retrieval_started_ms=(metrics.accepted_retrieval_started_ms or evidence.started_ms),
                retrieval_ready_ms=metrics.accepted_retrieval_ready_ms or evidence.completed_ms,
                controller_usage=metrics.trigger_usage,
                controller_calls=metrics.trigger_calls,
                controller_timeouts=metrics.trigger_timeouts,
                controller_failures=metrics.controller_failures,
                controller_elapsed_ms=metrics.trigger_elapsed_ms,
                retrieval_calls=metrics.retrieval_calls,
                raw_retrieval_calls=metrics.raw_retrieval_calls,
                settled_draft_retrievals=metrics.settled_draft_retrievals,
                retrieval_embedding_tokens=metrics.retrieval_embedding_tokens,
                retrieval_query_vector_ms=metrics.retrieval_query_vector_ms,
                retrieval_ann_ms=metrics.retrieval_ann_ms,
                retrieval_timeouts=metrics.retrieval_timeouts,
                retrieval_failures=metrics.retrieval_failures,
                stale_discards=metrics.stale_discards,
                trigger_cancellations=metrics.trigger_cancellations,
                unpriced_trigger_cancellations=metrics.unpriced_trigger_cancellations,
                retrieval_cancellations=metrics.retrieval_cancellations,
                evidence_reuses=metrics.evidence_reuses,
                evidence_revalidations=metrics.evidence_revalidations,
                commit_fallbacks=metrics.commit_fallbacks,
                first_speculative_started_ms=metrics.first_retrieval_started_ms,
                first_speculative_ready_ms=metrics.first_retrieval_ready_ms,
                accepted_revision=metrics.accepted_revision,
                accepted_from_fallback=metrics.accepted_from_fallback,
                accepted_ready_before_commit=metrics.accepted_ready_before_commit,
                accepted_speculative_completed_ms=(metrics.accepted_retrieval_completed_ms),
                commit_gate_ms=commit_gate_ms,
            ),
            committed_ms=committed_ms,
            request_started_ms=request_started_ms,
            send=send,
        )

    async def _answer(
        self,
        *,
        run_id: str,
        turn_id: str,
        path: str,
        request: CommitRequest,
        retrieval: PathTelemetry,
        committed_ms: float,
        request_started_ms: float,
        send,
    ) -> None:
        sources = self._sources(retrieval.result)
        accepted_lead_ms = max(0.0, committed_ms - retrieval.retrieval_ready_ms)
        accepted_candidate_lead_ms = (
            max(0.0, committed_ms - retrieval.accepted_speculative_completed_ms)
            if retrieval.accepted_speculative_completed_ms is not None
            else 0.0
        )
        speculative_lead_ms = (
            max(0.0, committed_ms - retrieval.first_speculative_ready_ms)
            if retrieval.first_speculative_ready_ms is not None
            else 0.0
        )
        reuse_mode = self._reuse_mode(path, retrieval, accepted_candidate_lead_ms)
        retrieval_embedding_tokens = retrieval.retrieval_embedding_tokens
        tool_embedding_tokens = 0
        incomplete_tool_traces: list[dict] = []
        embedding_usd = (
            retrieval_embedding_tokens * self.settings.embedding_input_per_million / 1_000_000
        )
        await send(
            {
                "type": "answer.started",
                "run_id": run_id,
                "turn_id": turn_id,
                "path": path,
                "sources": sources,
            }
        )
        first_token_ms = None
        answer = ""
        agent_usage = Usage()
        persistence_usage = Usage()
        tool_traces: list[dict] = []
        tool_started_ms: dict[str, float] = {}
        tool_attempt_count = 0
        tool_wall_ms = 0.0
        compression_calls: int | None = None
        summary_accounting_complete = False
        unpriced_summary_timeout_calls = 0
        generation_started_ms = time.perf_counter() * 1000
        answer_completed_ms: float | None = None
        persistence_started_ms: float | None = None
        persistence_completed_ms: float | None = None
        persistence_status = "not_started"
        event_stream = self.agent.stream(
            session_key=f"{request.session_id}:{path}",
            question=request.text,
            evidence=evidence_block(
                retrieval.result.hits,
                self.settings.context_token_budget,
            ),
            query_time=request.query_time,
            cache_scope=self._cache_scope(request.session_id, path),
        )
        events = aiter(event_stream)

        async def write_generation_failure(
            *,
            error_type: str,
            message: str,
            timed_out: bool,
        ) -> None:
            failed_ms = time.perf_counter() * 1000
            known_usage = Usage()
            known_usage.add(retrieval.controller_usage)
            known_usage.add(agent_usage)
            known_model_usd = model_cost(known_usage, self.settings).total_usd
            unpriced_generation_failures = int(not timed_out and answer_completed_ms is None)
            unpriced_local_tools = (
                len(incomplete_tool_traces) if tool_traces else tool_attempt_count
            )
            known_lower_bound = bool(
                timed_out
                or unpriced_generation_failures
                or unpriced_local_tools
                or retrieval.unpriced_trigger_cancellations
                or retrieval.retrieval_cancellations
                or retrieval.controller_timeouts
                or retrieval.controller_failures
                or retrieval.retrieval_timeouts
                or retrieval.retrieval_failures
            )
            await self.logger.write(
                {
                    "schema_version": 2,
                    "status": "failed",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "session_id": request.session_id,
                    "path": path,
                    "question": request.text,
                    "query_time": request.query_time,
                    "answer": answer,
                    "sources": sources,
                    "error": {
                        "stage": "generation",
                        "type": error_type,
                        "message": message,
                    },
                    "timing": {
                        "submit_to_first_token_ms": (
                            first_token_ms - committed_ms if first_token_ms is not None else None
                        ),
                        "elapsed_to_failure_ms": failed_ms - committed_ms,
                        "queue_before_path_ms": committed_ms - request_started_ms,
                        "retrieval_ms": retrieval.result.elapsed_ms,
                        "query_vector_ms": retrieval.result.query_vector_ms,
                        "ann_ms": retrieval.result.ann_ms,
                        "controller_ms": retrieval.controller_elapsed_ms,
                        "generation_elapsed_to_failure_ms": failed_ms - generation_started_ms,
                        "local_tool_wall_ms": tool_wall_ms,
                    },
                    "retrieval": {
                        "query": retrieval.result.query,
                        "embedding_tokens": retrieval_embedding_tokens,
                        "accepted_embedding_tokens": retrieval.result.embedding_tokens,
                        "cache_scope": retrieval.result.cache_scope,
                        "cache_hit": retrieval.result.cache_hit,
                        "calls": retrieval.retrieval_calls,
                        "timeouts": retrieval.retrieval_timeouts,
                        "failures": retrieval.retrieval_failures,
                    },
                    "controller": {
                        "calls": retrieval.controller_calls,
                        "timeouts": retrieval.controller_timeouts,
                        "failures": retrieval.controller_failures,
                        "elapsed_ms": retrieval.controller_elapsed_ms,
                        "cancellations": retrieval.trigger_cancellations,
                        "unpriced_cancellations": retrieval.unpriced_trigger_cancellations,
                    },
                    "reuse": {
                        "mode": reuse_mode,
                        "evidence_reuses": retrieval.evidence_reuses,
                        "evidence_revalidations": retrieval.evidence_revalidations,
                        "commit_fallbacks": retrieval.commit_fallbacks,
                        "stale_discards": retrieval.stale_discards,
                        "retrieval_cancellations": retrieval.retrieval_cancellations,
                    },
                    "persistence": {
                        "status": "not_started",
                        "elapsed_ms": None,
                        "compression_calls": None,
                        "summary_accounting_complete": None,
                    },
                    "tool_traces": tool_traces,
                    "usage": asdict(known_usage),
                    "estimated_cost_usd": {
                        "model": known_model_usd,
                        "query_embedding": embedding_usd,
                        "total": known_model_usd + embedding_usd,
                        "accounting_complete": False,
                        "known_cost_lower_bound": known_lower_bound,
                        "unpriced_generation_timeout_calls": int(timed_out),
                        "unpriced_generation_failure_calls": unpriced_generation_failures,
                        "unpriced_cancelled_controller_calls": (
                            retrieval.unpriced_trigger_cancellations
                        ),
                        "unpriced_cancelled_retrieval_calls": (retrieval.retrieval_cancellations),
                        "unpriced_controller_timeout_calls": retrieval.controller_timeouts,
                        "unpriced_controller_failure_calls": retrieval.controller_failures,
                        "unpriced_retrieval_timeout_calls": retrieval.retrieval_timeouts,
                        "unpriced_retrieval_failure_calls": retrieval.retrieval_failures,
                        "unpriced_local_tool_calls": unpriced_local_tools,
                        "unpriced_post_answer_persistence": False,
                        "unpriced_summary_timeout_calls": 0,
                    },
                }
            )

        try:
            try:
                async with asyncio.timeout(self.settings.answer_timeout_s):
                    while answer_completed_ms is None:
                        try:
                            event = await anext(events)
                        except StopAsyncIteration:
                            raise RuntimeError(
                                "grounded agent ended before generation completed"
                            ) from None
                        if event["type"] == "answer.delta":
                            if first_token_ms is None:
                                first_token_ms = time.perf_counter() * 1000
                            answer += event["text"]
                            await send(
                                {**event, "run_id": run_id, "turn_id": turn_id, "path": path}
                            )
                        elif event["type"] == "agent.completed":
                            answer = event["answer"]
                            agent_usage = event["usage"]
                            tool_traces = event.get("tool_traces", [])
                            answer_completed_ms = time.perf_counter() * 1000
                            sources = self._merge_tool_sources(sources, tool_traces)
                            tool_embedding_tokens = sum(
                                int(trace.get("embedding_tokens") or 0) for trace in tool_traces
                            )
                            incomplete_tool_traces = [
                                trace
                                for trace in tool_traces
                                if trace.get("accounting_complete") is not True
                            ]
                            embedding_usd = (
                                (retrieval_embedding_tokens + tool_embedding_tokens)
                                * self.settings.embedding_input_per_million
                                / 1_000_000
                            )
                            ready_usage = Usage()
                            ready_usage.add(retrieval.controller_usage)
                            ready_usage.add(agent_usage)
                            ready_model_usd = model_cost(ready_usage, self.settings).total_usd
                            await send(
                                {
                                    "type": "answer.ready",
                                    "run_id": run_id,
                                    "turn_id": turn_id,
                                    "path": path,
                                    "answer": answer,
                                    "sources": sources,
                                    "timing": {
                                        "submit_to_first_token_ms": (
                                            first_token_ms - committed_ms
                                            if first_token_ms is not None
                                            else None
                                        ),
                                        "total_response_ms": (answer_completed_ms - committed_ms),
                                        "accepted_retrieval_lead_at_commit_ms": (accepted_lead_ms),
                                        "accepted_candidate_retrieval_lead_ms": (
                                            accepted_candidate_lead_ms
                                        ),
                                    },
                                    "retrieval": {
                                        "cache_hit": retrieval.result.cache_hit,
                                        "calls": retrieval.retrieval_calls,
                                    },
                                    "controller": {
                                        "calls": retrieval.controller_calls,
                                    },
                                    "reuse": {
                                        "mode": reuse_mode,
                                        "commit_fallbacks": retrieval.commit_fallbacks,
                                    },
                                    "tool_traces": tool_traces,
                                    "estimated_cost_usd": {
                                        "model": ready_model_usd,
                                        "query_embedding": embedding_usd,
                                        "total": ready_model_usd + embedding_usd,
                                        "accounting_complete": False,
                                        "unpriced_post_answer_persistence": True,
                                    },
                                }
                            )
                        else:
                            if event["type"] == "agent.tool_started":
                                tool_attempt_count += 1
                                tool_started_ms[str(event.get("tool_call_id", "unknown"))] = (
                                    time.perf_counter() * 1000
                                )
                            elif event["type"] == "agent.tool_completed":
                                started = tool_started_ms.pop(
                                    str(event.get("tool_call_id", "unknown")),
                                    None,
                                )
                                if started is not None:
                                    tool_wall_ms += time.perf_counter() * 1000 - started
                            await send(
                                {**event, "run_id": run_id, "turn_id": turn_id, "path": path}
                            )
            except TimeoutError:
                await write_generation_failure(
                    error_type="TimeoutError",
                    message="grounded answer timed out",
                    timed_out=True,
                )
                raise RuntimeError("grounded answer timed out") from None
            except Exception as exc:
                await write_generation_failure(
                    error_type=type(exc).__name__,
                    message=str(exc) or "grounded answer failed",
                    timed_out=False,
                )
                raise

            persistence_started_ms = time.perf_counter() * 1000
            persistence_status = "in_progress"
            try:
                async with asyncio.timeout(self.settings.post_answer_persistence_timeout_s):
                    while True:
                        try:
                            event = await anext(events)
                        except StopAsyncIteration:
                            break
                        if event["type"] == "agent.context_compressed":
                            await send(
                                {**event, "run_id": run_id, "turn_id": turn_id, "path": path}
                            )
                        elif event["type"] == "agent.persisted":
                            persistence_usage = event["usage"]
                            compression_calls = int(event["compression_calls"])
                            summary_accounting_complete = (
                                event.get("summary_accounting_complete") is True
                            )
                            unpriced_summary_timeout_calls = int(
                                event.get("unpriced_summary_timeout_calls") or 0
                            )
                            persistence_status = "completed"
                        else:
                            raise RuntimeError(
                                f"unexpected post-answer agent event: {event['type']}"
                            )
                if persistence_status != "completed":
                    raise RuntimeError("grounded agent ended before persistence completed")
            except TimeoutError:
                persistence_status = "timeout"
                logger.warning(
                    "post-answer persistence timed out for run_id=%s path=%s",
                    run_id,
                    path,
                )
            except Exception:
                persistence_status = "failed"
                logger.exception(
                    "post-answer persistence failed for run_id=%s path=%s",
                    run_id,
                    path,
                )
            finally:
                persistence_completed_ms = time.perf_counter() * 1000
        finally:
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()

        if answer_completed_ms is None or persistence_completed_ms is None:
            raise RuntimeError("grounded answer lifecycle ended without completion timestamps")

        usage = Usage()
        usage.add(retrieval.controller_usage)
        usage.add(agent_usage)
        usage.add(persistence_usage)
        model_usd = model_cost(usage, self.settings).total_usd
        embedding_tokens = retrieval_embedding_tokens + tool_embedding_tokens
        embedding_usd = embedding_tokens * self.settings.embedding_input_per_million / 1_000_000
        accounting_complete = (
            retrieval.unpriced_trigger_cancellations == 0
            and retrieval.retrieval_cancellations == 0
            and retrieval.controller_timeouts == 0
            and retrieval.controller_failures == 0
            and retrieval.retrieval_timeouts == 0
            and retrieval.retrieval_failures == 0
            and not incomplete_tool_traces
            and persistence_status == "completed"
            and summary_accounting_complete
        )
        timing = {
            "submit_to_first_token_ms": (
                first_token_ms - committed_ms if first_token_ms is not None else None
            ),
            "total_response_ms": answer_completed_ms - committed_ms,
            "queue_before_path_ms": committed_ms - request_started_ms,
            "retrieval_ms": retrieval.result.elapsed_ms,
            "query_vector_ms": retrieval.result.query_vector_ms,
            "ann_ms": retrieval.result.ann_ms,
            "all_retrieval_query_vector_ms": retrieval.retrieval_query_vector_ms,
            "all_retrieval_ann_ms": retrieval.retrieval_ann_ms,
            "controller_ms": retrieval.controller_elapsed_ms,
            "accepted_retrieval_lead_at_commit_ms": accepted_lead_ms,
            "accepted_candidate_retrieval_lead_ms": accepted_candidate_lead_ms,
            "first_speculative_retrieval_lead_ms": speculative_lead_ms,
            "commit_gate_ms": retrieval.commit_gate_ms,
            "generation_to_first_token_ms": (
                first_token_ms - generation_started_ms if first_token_ms is not None else None
            ),
            "generation_ms": answer_completed_ms - generation_started_ms,
            "post_answer_persistence_ms": (
                persistence_completed_ms - persistence_started_ms
                if persistence_started_ms is not None
                else None
            ),
            "local_tool_wall_ms": tool_wall_ms,
        }
        record = {
            "schema_version": 2,
            "run_id": run_id,
            "turn_id": turn_id,
            "session_id": request.session_id,
            "path": path,
            "question": request.text,
            "query_time": request.query_time,
            "answer": answer,
            "sources": sources,
            "timing": timing,
            "retrieval": {
                "query": retrieval.result.query,
                "embedding_tokens": retrieval_embedding_tokens,
                "accepted_embedding_tokens": retrieval.result.embedding_tokens,
                "cache_scope": retrieval.result.cache_scope,
                "cache_hit": retrieval.result.cache_hit,
                "query_vector_ms": retrieval.result.query_vector_ms,
                "ann_ms": retrieval.result.ann_ms,
                "all_query_vector_ms": retrieval.retrieval_query_vector_ms,
                "all_ann_ms": retrieval.retrieval_ann_ms,
                "started_ms": retrieval.retrieval_started_ms,
                "ready_ms": retrieval.retrieval_ready_ms,
                "calls": retrieval.retrieval_calls,
                "raw_candidate_calls": retrieval.raw_retrieval_calls,
                "settled_draft_retrievals": retrieval.settled_draft_retrievals,
                "timeouts": retrieval.retrieval_timeouts,
                "failures": retrieval.retrieval_failures,
                "first_speculative_started_ms": retrieval.first_speculative_started_ms,
                "first_speculative_ready_ms": retrieval.first_speculative_ready_ms,
                "accepted_revision": retrieval.accepted_revision,
                "accepted_from_fallback": retrieval.accepted_from_fallback,
                "accepted_ready_before_commit": retrieval.accepted_ready_before_commit,
            },
            "controller": {
                "calls": retrieval.controller_calls,
                "timeouts": retrieval.controller_timeouts,
                "failures": retrieval.controller_failures,
                "elapsed_ms": retrieval.controller_elapsed_ms,
                "cancellations": retrieval.trigger_cancellations,
                "unpriced_cancellations": retrieval.unpriced_trigger_cancellations,
            },
            "reuse": {
                "mode": reuse_mode,
                "evidence_reuses": retrieval.evidence_reuses,
                "evidence_revalidations": retrieval.evidence_revalidations,
                "commit_fallbacks": retrieval.commit_fallbacks,
                "stale_discards": retrieval.stale_discards,
                "retrieval_cancellations": retrieval.retrieval_cancellations,
            },
            "persistence": {
                "status": persistence_status,
                "elapsed_ms": timing["post_answer_persistence_ms"],
                "compression_calls": compression_calls,
                "summary_accounting_complete": summary_accounting_complete,
                "unpriced_summary_timeout_calls": unpriced_summary_timeout_calls,
            },
            "tool_traces": tool_traces,
            "usage": asdict(usage),
            "estimated_cost_usd": {
                "model": model_usd,
                "query_embedding": embedding_usd,
                "total": model_usd + embedding_usd,
                "accounting_complete": accounting_complete,
                "unpriced_cancelled_controller_calls": (retrieval.unpriced_trigger_cancellations),
                "unpriced_cancelled_retrieval_calls": retrieval.retrieval_cancellations,
                "unpriced_controller_timeout_calls": retrieval.controller_timeouts,
                "unpriced_controller_failure_calls": retrieval.controller_failures,
                "unpriced_retrieval_timeout_calls": retrieval.retrieval_timeouts,
                "unpriced_retrieval_failure_calls": retrieval.retrieval_failures,
                "unpriced_local_tool_calls": len(incomplete_tool_traces),
                "unpriced_post_answer_persistence": persistence_status != "completed",
                "unpriced_summary_timeout_calls": unpriced_summary_timeout_calls,
            },
        }
        await self.logger.write(record)
        self.counters.path_completions += 1
        await send(
            {
                "type": "answer.completed",
                "run_id": run_id,
                "turn_id": turn_id,
                "path": path,
                "answer": answer,
                "sources": sources,
                "timing": timing,
                "retrieval": record["retrieval"],
                "controller": record["controller"],
                "reuse": record["reuse"],
                "persistence": record["persistence"],
                "tool_traces": tool_traces,
                "usage": record["usage"],
                "estimated_cost_usd": record["estimated_cost_usd"],
            }
        )

    @staticmethod
    def _sources(result: SearchResult) -> list[dict]:
        return [
            {
                "chunk_id": hit.chunk.chunk_id,
                "title": hit.chunk.title,
                "url": hit.chunk.url,
                "score": round(hit.score, 6),
            }
            for hit in result.hits
        ]

    @staticmethod
    def _merge_tool_sources(sources: list[dict], tool_traces: list[dict]) -> list[dict]:
        merged = list(sources)
        seen = {source["chunk_id"] for source in merged}
        for trace in tool_traces:
            for source in trace.get("sources", []):
                if source["chunk_id"] not in seen:
                    seen.add(source["chunk_id"])
                    merged.append(source)
        return merged

    @staticmethod
    def _reuse_mode(
        path: str,
        retrieval: PathTelemetry,
        accepted_candidate_lead_ms: float,
    ) -> str:
        if (
            path == "stream"
            and retrieval.accepted_from_fallback is False
            and retrieval.accepted_ready_before_commit
        ):
            return (
                "precommit_revalidated" if retrieval.evidence_revalidations else "precommit_exact"
            )
        if (
            path == "stream"
            and retrieval.accepted_from_fallback is False
            and accepted_candidate_lead_ms > 0
        ):
            return "presubmit_retrieval_revalidated_at_commit"
        if path == "stream" and retrieval.accepted_from_fallback is False:
            return "inflight_completed_postcommit"
        return "committed_text_retrieval"

    def _schedule_channel_cleanup(self, key: str, delay_s: float = 300.0) -> None:
        async def cleanup() -> None:
            await asyncio.sleep(delay_s)
            await self.events.close_and_remove(key)

        task = asyncio.create_task(cleanup())
        self.maintenance_tasks.add(task)
        task.add_done_callback(self.maintenance_tasks.discard)

    def _prune_terminal_turns_locked(self, *, protected_turn_id: str | None = None) -> None:
        """Bound completed tombstones without evicting in-progress commit setup."""
        overflow = len(self.terminal_turns) - self.terminal_turn_limit
        if overflow <= 0:
            return
        for turn_id, reservation in tuple(self.terminal_turns.items()):
            if overflow <= 0:
                return
            if (
                turn_id == protected_turn_id
                or turn_id in self.turn_tasks
                or (reservation is not None and not reservation.started)
            ):
                continue
            self.terminal_turns.pop(turn_id, None)
            overflow -= 1

    async def _prune_terminal_turns(self) -> None:
        async with self._turn_lock:
            self._prune_terminal_turns_locked()

    async def cancel_turn(self, turn_id: str) -> None:
        self._validate_turn_id(turn_id)
        async with self._turn_lock:
            pending = self.terminal_turns.get(turn_id)
            if pending is not None and not pending.started:
                pending.setup_error = "turn cancelled during commit setup"
                pending.ready.set()
            self.terminal_turns[turn_id] = None
            self._prune_terminal_turns_locked(protected_turn_id=turn_id)
            run_tasks = list(self.turn_tasks.pop(turn_id, ()))
            coordinator = self.turns.pop(turn_id, None)
            self.turn_bindings.pop(turn_id, None)
        for task in run_tasks:
            task.cancel()
        if run_tasks:
            await asyncio.gather(*run_tasks, return_exceptions=True)
        if coordinator is not None:
            await coordinator.close()
        await self.events.close_and_remove(f"turn:{turn_id}")

    async def shutdown(self) -> None:
        for task in [*self.tasks, *self.maintenance_tasks]:
            task.cancel()
        await asyncio.gather(
            *self.tasks,
            *self.maintenance_tasks,
            return_exceptions=True,
        )
        for coordinator in self.turns.values():
            await coordinator.close()
        await self.agent.close()
        await self.trigger.close()
        await self.store.close()
