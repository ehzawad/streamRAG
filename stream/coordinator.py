from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from shared.data.vector_store import QdrantVectorStore
from shared.models import InputSnapshot, SearchResult, Usage
from shared.query import bounded_retrieval_query
from stream.config import StreamSettings
from stream.snapshot import analyze_delta
from stream.trigger import ModelTrigger

Send = Callable[[dict], Awaitable[None]]

TIMELINE_LIMIT = 400
ATTEMPT_LIMIT = 64
TIMELINE_QUERY_CHARS = 160
ATTEMPT_QUERY_CHARS = 300

_QUESTION_OPENERS = frozenset(
    {
        "can",
        "could",
        "did",
        "do",
        "does",
        "how",
        "is",
        "tell",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "would",
    }
)
_QUERY_FUNCTION_WORDS = _QUESTION_OPENERS | frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "been",
        "being",
        "by",
        "currently",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "it",
        "its",
        "me",
        "of",
        "on",
        "or",
        "please",
        "than",
        "that",
        "the",
        "their",
        "this",
        "to",
        "with",
    }
)


def has_terminal_boundary(text: str, last_trigger_text: str) -> bool:
    """Treat a completed typed sentence as a fresh model-decision boundary."""
    return text != last_trigger_text and text.rstrip().endswith(("?", "!", "."))


def meaningful_completed_prefix(text: str, *, minimum_words: int) -> str | None:
    """Return a recall-first raw query without embedding a partial final token."""
    stripped = text.rstrip()
    if not stripped:
        return None
    if text[-1].isspace() or stripped.endswith(("?", "!", ".", ",", ":", ";")):
        candidate = stripped
    else:
        candidate, separator, _partial = stripped.rpartition(" ")
        if not separator:
            return None
        candidate = candidate.rstrip()
    words = candidate.split()
    if len(words) < minimum_words:
        return None
    normalized = [word.casefold().strip("'\"()[]{}.,?!:;") for word in words]
    if not set(normalized[:4]).intersection(_QUESTION_OPENERS):
        return None
    if sum(word not in _QUERY_FUNCTION_WORDS for word in normalized) < 2:
        return None
    return candidate


@dataclass
class RetrievalEvidence:
    source_text: str
    revision: int
    query: str
    result: SearchResult
    started_ms: float
    completed_ms: float
    validated_ms: float | None = None
    controller_validated: bool = True
    commit_safe_exact: bool = False
    retrieval_id: str = ""
    origin: str = "trigger"


@dataclass(frozen=True)
class EvidencePromotion:
    snapshot: InputSnapshot
    query: str
    validated_ms: float


@dataclass
class StreamMetrics:
    started_ms: float = field(default_factory=lambda: time.perf_counter() * 1000)
    trigger_usage: Usage = field(default_factory=Usage)
    trigger_calls: int = 0
    retrieval_calls: int = 0
    raw_retrieval_calls: int = 0
    settled_draft_retrievals: int = 0
    retrieval_embedding_tokens: int = 0
    retrieval_query_vector_ms: float = 0.0
    retrieval_ann_ms: float = 0.0
    stale_discards: int = 0
    trigger_timeouts: int = 0
    controller_failures: int = 0
    trigger_elapsed_ms: float = 0.0
    retrieval_timeouts: int = 0
    retrieval_failures: int = 0
    trigger_cancellations: int = 0
    unpriced_trigger_cancellations: int = 0
    retrieval_cancellations: int = 0
    evidence_reuses: int = 0
    evidence_revalidations: int = 0
    commit_fallbacks: int = 0
    first_retrieval_started_ms: float | None = None
    first_retrieval_ready_ms: float | None = None
    accepted_retrieval_started_ms: float | None = None
    accepted_retrieval_completed_ms: float | None = None
    accepted_retrieval_ready_ms: float | None = None
    accepted_revision: int | None = None
    accepted_from_fallback: bool | None = None
    accepted_ready_before_commit: bool | None = None
    commit_ms: float | None = None
    commit_branch: str | None = None
    fallback_reason: str | None = None
    state_at_commit: dict | None = None
    inflight_wait: dict | None = None
    accepted_origin: str | None = None
    accepted_query: str | None = None
    retrieval_attempts: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    timeline_dropped: int = 0


class StreamCoordinator:
    """Per-turn speculative retrieval with single-flight, correction-aware gates."""

    def __init__(
        self,
        *,
        turn_id: str,
        index: QdrantVectorStore,
        trigger: ModelTrigger,
        settings: StreamSettings,
        send: Send,
        conversation_context: str = "",
        cache_scope: str = "stream",
    ):
        if not cache_scope.strip():
            raise ValueError("cache scope is empty")
        self.turn_id = turn_id
        self.index = index
        self.trigger = trigger
        self.settings = settings
        self.send = send
        self.conversation_context = conversation_context
        self.cache_scope = cache_scope
        self.last_activity_ms = time.perf_counter() * 1000
        self.latest = InputSnapshot(turn_id=turn_id, revision=0, text="")
        self.last_trigger_text = ""
        self.last_trigger_ms = 0.0
        self.previous_query: str | None = None
        self.evidence: RetrievalEvidence | None = None
        self.metrics = StreamMetrics()
        self._trigger_task: asyncio.Task | None = None
        self._trigger_call_states: dict[asyncio.Task, str] = {}
        self._retrieval_task: asyncio.Task | None = None
        self._retrieval_cancel_requested: set[asyncio.Task] = set()
        self._seq = 0
        self._trigger_count = 0
        self._retrieval_count = 0
        self._pending_events: list[dict] = []
        self._trigger_ids: dict[asyncio.Task, str] = {}
        self._retrieval_ids: dict[asyncio.Task, str] = {}
        self._attempts_by_id: dict[str, dict] = {}
        self._retrieval_query: str | None = None
        self._retrieval_source_text: str | None = None
        self._retrieval_controller_validated = True
        self._retrieval_commit_safe_exact = False
        self._pending_promotion: EvidencePromotion | None = None
        self._pending_snapshot: InputSnapshot | None = None
        self._quiet_task: asyncio.Task | None = None
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._closed = False
        self._committed = False

    @staticmethod
    def _active(task: asyncio.Task | None) -> bool:
        return task is not None and not task.done()

    @property
    def committed(self) -> bool:
        return self._committed

    @property
    def _active_retrieval_id(self) -> str | None:
        task = self._retrieval_task
        return self._retrieval_ids.get(task) if task is not None else None

    def _record_timeline(self, event: dict) -> None:
        entry = {"t_ms": time.perf_counter() * 1000, **event}
        query = entry.get("query")
        if isinstance(query, str) and len(query) > TIMELINE_QUERY_CHARS:
            entry["query"] = query[:TIMELINE_QUERY_CHARS]
        timeline = self.metrics.timeline
        if len(timeline) >= TIMELINE_LIMIT:
            # Drop the oldest entry: the commit-phase tail is the forensic payload.
            del timeline[0]
            self.metrics.timeline_dropped += 1
        timeline.append(entry)

    def _stamp(self, payload: dict) -> dict:
        self._seq += 1
        event = {"seq": self._seq, "actor": "coordinator", **payload}
        self._record_timeline(event)
        return event

    def _note(self, payload: dict) -> None:
        """Record a lifecycle mutation from a synchronous path; flushed with the next emit."""
        self._pending_events.append(self._stamp(payload))

    async def _emit(self, payload: dict) -> None:
        event = self._stamp(payload)
        if self._pending_events:
            pending, self._pending_events = self._pending_events, []
            for queued in pending:
                await self.send(queued)
        await self.send(event)

    def _register_attempt(
        self,
        retrieval_id: str,
        *,
        origin: str,
        query: str,
        source_revision: int,
        controller_validated: bool,
        commit_safe_exact: bool,
    ) -> dict:
        attempt = {
            "retrieval_id": retrieval_id,
            "origin": origin,
            "query": query[:ATTEMPT_QUERY_CHARS],
            "source_revision": source_revision,
            "controller_validated": controller_validated,
            "commit_safe_exact": commit_safe_exact,
            "created_ms": time.perf_counter() * 1000,
            "outcome": "pending",
        }
        self._attempts_by_id[retrieval_id] = attempt
        if len(self.metrics.retrieval_attempts) < ATTEMPT_LIMIT:
            self.metrics.retrieval_attempts.append(attempt)
        return attempt

    def _finish_attempt(self, retrieval_id: str | None, outcome: str, **extra) -> None:
        attempt = self._attempts_by_id.get(retrieval_id) if retrieval_id else None
        if attempt is None or attempt["outcome"] != "pending":
            return
        attempt["outcome"] = outcome
        attempt["finished_ms"] = time.perf_counter() * 1000
        attempt.update(extra)

    def _compatible_with_latest(self, snapshot: InputSnapshot) -> bool:
        return (
            snapshot.turn_id == self.turn_id
            and snapshot.revision <= self.latest.revision
            and self.latest.text.startswith(snapshot.text)
        )

    def _cancel_for_correction(self) -> None:
        """Invalidate work only when the user changed existing text."""
        self._cancel_quiet_task()
        if self._active(self._trigger_task):
            self._cancel_trigger_task(self._trigger_task, reason="non_append_correction")
        if self._active(self._retrieval_task):
            self._cancel_retrieval_task(self._retrieval_task, reason="non_append_correction")
        if self.evidence is not None:
            self._note(
                {
                    "type": "evidence.invalidated",
                    "evidence_id": self.evidence.retrieval_id,
                    "query": self.evidence.query,
                    "reason": "non_append_correction",
                }
            )
        self._pending_snapshot = None
        self._pending_promotion = None
        self.previous_query = None
        self.evidence = None
        # A correction is a new decision boundary even if the previous trigger
        # started less than one sampling interval ago.
        self.last_trigger_text = ""
        self.last_trigger_ms = 0.0

    def _within_call_budget(self, snapshot: InputSnapshot) -> bool:
        word_count = len(snapshot.text.split())
        terminal_boundary = has_terminal_boundary(snapshot.text, self.last_trigger_text)
        # Long typed questions expose more materially different prefixes. Start
        # from the configured four-call budget, add one decision per six words
        # beyond twelve (at most three), and reserve one terminal-prefix decision.
        adaptive_limit = self.settings.trigger_max_presubmit_calls + min(
            3,
            max(0, (word_count - 7) // 6),
        )
        return self.metrics.trigger_calls < adaptive_limit or (
            terminal_boundary and self.metrics.trigger_calls < adaptive_limit + 1
        )

    def _cancel_quiet_task(self) -> None:
        task = self._quiet_task
        self._quiet_task = None
        if self._active(task):
            task.cancel()
            self._track_cleanup(task)

    def _arm_quiet_period(self, snapshot: InputSnapshot) -> None:
        self._quiet_task = asyncio.create_task(self._quiet_worker(snapshot))

    async def _quiet_worker(self, snapshot: InputSnapshot) -> None:
        try:
            await asyncio.sleep(self.settings.settled_draft_delay_ms / 1000)
            if (
                self._closed
                or self._committed
                or self._quiet_task is not asyncio.current_task()
                or self.latest.revision != snapshot.revision
                or self.latest.text != snapshot.text
            ):
                return
            await self._start_settled_draft_retrieval(snapshot)
        except asyncio.CancelledError:
            return
        finally:
            if self._quiet_task is asyncio.current_task():
                self._quiet_task = None

    async def _start_settled_draft_retrieval(self, snapshot: InputSnapshot) -> None:
        """Move exact retrieval into a quiet typing pause without generating an answer."""
        if self._closed or self._committed or self.latest != snapshot:
            return

        self._pending_snapshot = None
        if self._active(self._trigger_task):
            task = self._trigger_task
            self._cancel_trigger_task(task, reason="settled_draft_takeover")
            self._trigger_task = None
            self._track_cleanup(task)

        query = bounded_retrieval_query(snapshot.text)
        evidence = self.evidence
        if (
            evidence is not None
            and evidence.source_text == snapshot.text
            and evidence.query == query
        ):
            if not evidence.commit_safe_exact:
                self.evidence = RetrievalEvidence(
                    source_text=evidence.source_text,
                    revision=evidence.revision,
                    query=evidence.query,
                    result=evidence.result,
                    started_ms=evidence.started_ms,
                    completed_ms=evidence.completed_ms,
                    validated_ms=evidence.validated_ms,
                    controller_validated=evidence.controller_validated,
                    commit_safe_exact=True,
                    retrieval_id=evidence.retrieval_id,
                    origin=evidence.origin,
                )
                self.metrics.evidence_revalidations += 1
            self.previous_query = query
            await self._emit(
                {
                    "type": "draft.settled",
                    "revision": snapshot.revision,
                    "query": query,
                    "state": "ready",
                    "retrieval_id": evidence.retrieval_id,
                }
            )
            return

        if (
            self._active(self._retrieval_task)
            and self._retrieval_source_text == snapshot.text
            and self._retrieval_query is not None
            and self._retrieval_query == query
        ):
            self._retrieval_commit_safe_exact = True
            self.previous_query = query
            await self._emit(
                {
                    "type": "draft.settled",
                    "revision": snapshot.revision,
                    "query": query,
                    "state": "in_flight",
                    "retrieval_id": self._active_retrieval_id,
                }
            )
            return

        await self._cancel_active_retrieval(reason="settled_draft_takeover")
        if self._closed or self._committed or self.latest != snapshot:
            return
        self.previous_query = query
        self.metrics.settled_draft_retrievals += 1
        retrieval_id = self._start_retrieval(
            snapshot,
            query,
            controller_validated=False,
            commit_safe_exact=True,
            origin="settled_exact",
        )
        await self._emit(
            {
                "type": "draft.settled",
                "revision": snapshot.revision,
                "query": query,
                "state": "starting",
                "retrieval_id": retrieval_id,
            }
        )

    def _eligible(self, snapshot: InputSnapshot, *, now_ms: float) -> bool:
        delta = analyze_delta(self.latest.text, snapshot.text)
        # update() installs snapshot as latest before calling this helper, so use
        # the last dispatched trigger text for the material-change calculation.
        trigger_delta = analyze_delta(self.last_trigger_text, snapshot.text)
        terminal_boundary = has_terminal_boundary(snapshot.text, self.last_trigger_text)
        return (
            delta.word_count >= self.settings.trigger_min_tokens
            and (
                trigger_delta.new_words >= self.settings.trigger_min_new_tokens or terminal_boundary
            )
            and now_ms - self.last_trigger_ms >= self.settings.trigger_interval_ms
            and self._within_call_budget(snapshot)
        )

    async def update(self, snapshot: InputSnapshot) -> None:
        if self._closed or self._committed or snapshot.turn_id != self.turn_id:
            return
        if snapshot.revision <= self.latest.revision and self.latest.text:
            return
        self._cancel_quiet_task()
        prior = self.latest.text
        self.last_activity_ms = time.perf_counter() * 1000
        delta = analyze_delta(prior, snapshot.text)
        self.latest = snapshot
        if prior and not delta.append_only:
            self._cancel_for_correction()
        await self._emit(
            {
                "type": "input.ack",
                "turn_id": self.turn_id,
                "revision": snapshot.revision,
                "chars": len(snapshot.text),
                "append_only": delta.append_only,
            }
        )
        if (
            self._closed
            or self._committed
            or self.latest.revision != snapshot.revision
            or self.latest.text != snapshot.text
        ):
            return
        self._arm_quiet_period(snapshot)
        now = time.perf_counter() * 1000
        if not self._eligible(snapshot, now_ms=now):
            return
        if self._active(self._trigger_task):
            # Coalesce normal append-only typing. The active decision remains
            # useful because its input is a prefix of the latest draft.
            self._pending_snapshot = snapshot
            self._note(
                {
                    "type": "snapshot.coalesced",
                    "revision": snapshot.revision,
                    "reason": "trigger_in_flight",
                    "trigger_id": self._trigger_ids.get(self._trigger_task, "trigger-?"),
                }
            )
            return
        self._start_trigger(snapshot)

    def freeze_at_commit(self, snapshot: InputSnapshot, committed_ms: float | None = None) -> None:
        """Freeze the final revision at server receipt so late snapshots cannot gain lead."""
        if snapshot.turn_id != self.turn_id:
            raise ValueError("turn_id changed at commit")
        if self._committed:
            if snapshot.revision != self.latest.revision or snapshot.text != self.latest.text:
                raise ValueError("committed snapshot changed")
            return
        self._cancel_quiet_task()
        prior = self.latest.text
        correction_at_commit = bool(
            prior and snapshot.text != prior and not snapshot.text.startswith(prior)
        )
        evidence = self.evidence
        self.metrics.state_at_commit = {
            "trigger_active": self._active(self._trigger_task),
            "retrieval_active": self._active(self._retrieval_task),
            "evidence_present": evidence is not None,
            "evidence_validated": bool(
                evidence and (evidence.controller_validated or evidence.commit_safe_exact)
            ),
            "correction_at_commit": correction_at_commit,
            "latest_revision_before_commit": self.latest.revision,
            "committed_revision": snapshot.revision,
            "committed_suffix_chars": (
                len(snapshot.text) - len(prior) if snapshot.text.startswith(prior) else None
            ),
        }
        if correction_at_commit:
            self._cancel_for_correction()
        self.latest = snapshot
        self.metrics.commit_ms = committed_ms or time.perf_counter() * 1000
        self.last_activity_ms = self.metrics.commit_ms
        self._committed = True

    def _start_trigger(self, snapshot: InputSnapshot) -> None:
        self.last_trigger_text = snapshot.text
        self.last_trigger_ms = time.perf_counter() * 1000
        raw_prefix: str | None = None
        candidate_query = self._validation_candidate_query(snapshot)
        if (
            self.settings.parallel_raw_retrieval
            and candidate_query is None
            and self.evidence is None
            and not self._active(self._retrieval_task)
        ):
            raw_prefix = meaningful_completed_prefix(
                snapshot.text,
                minimum_words=self.settings.trigger_min_tokens,
            )
            candidate_query = bounded_retrieval_query(raw_prefix) if raw_prefix else None
        self._trigger_count += 1
        trigger_id = f"trigger-{self._trigger_count}"
        self._trigger_task = asyncio.create_task(self._trigger_worker(snapshot, candidate_query))
        self._trigger_call_states[self._trigger_task] = "pending"
        self._trigger_ids[self._trigger_task] = trigger_id
        if candidate_query is not None and raw_prefix is not None:
            raw_snapshot = snapshot.model_copy(update={"text": raw_prefix})
            self._start_retrieval(
                raw_snapshot,
                candidate_query,
                controller_validated=False,
                origin="raw_prefix",
            )

    async def _trigger_worker(
        self,
        snapshot: InputSnapshot,
        candidate_query: str | None,
    ):
        try:
            return await self._run_trigger(snapshot, candidate_query)
        finally:
            current = asyncio.current_task()
            self._trigger_call_states.pop(current, None)
            self._trigger_ids.pop(current, None)
            if self._trigger_task is current:
                self._trigger_task = None
                pending = self._pending_snapshot
                self._pending_snapshot = None
                if (
                    pending is not None
                    and not self._closed
                    and not self._committed
                    and self._compatible_with_latest(pending)
                    and self._within_call_budget(pending)
                ):
                    self._start_trigger(pending)

    async def _run_trigger(
        self,
        snapshot: InputSnapshot,
        candidate_query: str | None,
    ):
        self.metrics.trigger_calls += 1
        trigger_id = self._trigger_ids.get(asyncio.current_task(), "trigger-?")
        controller_started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                self.trigger.decide(
                    draft=snapshot.text,
                    previous_query=candidate_query or self.previous_query,
                    conversation_context=self.conversation_context,
                ),
                timeout=self.settings.trigger_timeout_s,
            )
        except asyncio.CancelledError:
            return None
        except TimeoutError:
            self.metrics.trigger_timeouts += 1
            self._set_trigger_call_state("timeout")
            await self._emit(
                {
                    "type": "trigger.error",
                    "revision": snapshot.revision,
                    "trigger_id": trigger_id,
                    "reason": "timeout",
                    "actor": "trigger_model",
                }
            )
            return None
        except Exception:
            self.metrics.controller_failures += 1
            self._set_trigger_call_state("failure")
            await self._emit(
                {
                    "type": "trigger.error",
                    "revision": snapshot.revision,
                    "trigger_id": trigger_id,
                    "reason": "failure",
                    "actor": "trigger_model",
                }
            )
            return None
        finally:
            self.metrics.trigger_elapsed_ms += (time.perf_counter() - controller_started) * 1000
        self.metrics.trigger_usage.add(result.usage)
        self._set_trigger_call_state("accounted")
        if self._closed or self._committed:
            # The turn froze while this decision was in flight. Consuming it now
            # could start post-commit speculative work that no one can use.
            self.metrics.stale_discards += 1
            self._note(
                {
                    "type": "trigger.discarded",
                    "trigger_id": trigger_id,
                    "reason": "post_commit",
                }
            )
            return None
        if not self._compatible_with_latest(snapshot):
            self.metrics.stale_discards += 1
            self._note(
                {
                    "type": "trigger.discarded",
                    "trigger_id": trigger_id,
                    "reason": "snapshot_superseded",
                }
            )
            return None
        decision = result.decision
        await self._emit(
            {
                "type": "trigger.decision",
                "revision": snapshot.revision,
                "trigger_id": trigger_id,
                "action": decision.action,
                "query": decision.retrieval_query,
                "candidate_query": candidate_query,
                "candidate_query_compatible": decision.candidate_query_compatible,
                "elapsed_ms": round(result.elapsed_ms, 2),
                "actor": "trigger_model",
            }
        )
        if candidate_query and decision.candidate_query_compatible:
            self.previous_query = candidate_query
            await self._revalidate_query(
                snapshot,
                candidate_query,
            )
        elif decision.action == "retrieve" and decision.retrieval_query:
            self.previous_query = decision.retrieval_query
            if candidate_query and self._same_query(candidate_query, decision.retrieval_query):
                await self._revalidate_query(
                    snapshot,
                    candidate_query,
                )
            else:
                self._start_retrieval(snapshot, decision.retrieval_query)
        elif decision.action == "keep_previous":
            if candidate_query is not None:
                self.previous_query = candidate_query
            await self._revalidate_previous(snapshot)
        elif candidate_query is not None:
            await self._discard_unvalidated_candidate(candidate_query)
        return decision

    async def _discard_unvalidated_candidate(self, query: str) -> None:
        evidence = self.evidence
        if (
            evidence is not None
            and not evidence.controller_validated
            and self._same_query(evidence.query, query)
        ):
            self._note(
                {
                    "type": "evidence.invalidated",
                    "evidence_id": evidence.retrieval_id,
                    "query": evidence.query,
                    "reason": "candidate_rejected",
                }
            )
            self.evidence = None
        if (
            self._active(self._retrieval_task)
            and not self._retrieval_controller_validated
            and self._retrieval_query is not None
            and self._same_query(self._retrieval_query, query)
        ):
            await self._cancel_active_retrieval(reason="candidate_rejected")

    @staticmethod
    def _same_query(left: str, right: str) -> bool:
        return " ".join(left.casefold().split()) == " ".join(right.casefold().split())

    async def _revalidate_previous(self, snapshot: InputSnapshot) -> None:
        """Promote prefix evidence only after an explicit keep_previous decision."""
        query = self.previous_query
        if not query or not self._compatible_with_latest(snapshot):
            return
        await self._revalidate_query(snapshot, query)

    async def _revalidate_query(
        self,
        snapshot: InputSnapshot,
        query: str,
    ) -> None:
        """Promote compatible prefix work after the controller confirms its query."""
        if not self._compatible_with_latest(snapshot):
            return
        validated_ms = time.perf_counter() * 1000
        evidence = self.evidence
        if (
            evidence is not None
            and self._same_query(evidence.query, query)
            and snapshot.text.startswith(evidence.source_text)
        ):
            from_revision = evidence.revision
            self.evidence = RetrievalEvidence(
                source_text=snapshot.text,
                revision=snapshot.revision,
                query=evidence.query,
                result=evidence.result,
                started_ms=evidence.started_ms,
                completed_ms=evidence.completed_ms,
                validated_ms=max(evidence.completed_ms, validated_ms),
                controller_validated=True,
                retrieval_id=evidence.retrieval_id,
                origin=evidence.origin,
            )
            self.metrics.evidence_revalidations += 1
            await self._emit(
                {
                    "type": "retrieval.revalidated",
                    "revision": snapshot.revision,
                    "retrieval_id": evidence.retrieval_id,
                    "query": evidence.query,
                    "state": "ready",
                    "from_revision": from_revision,
                    "to_revision": snapshot.revision,
                    "reason": "controller_confirmed",
                    "actor": "trigger_model",
                }
            )
            return
        if (
            self._active(self._retrieval_task)
            and self._retrieval_query is not None
            and self._same_query(self._retrieval_query, query)
            and self._retrieval_source_text is not None
            and snapshot.text.startswith(self._retrieval_source_text)
        ):
            retrieval_id = self._active_retrieval_id
            attempt = self._attempts_by_id.get(retrieval_id) if retrieval_id else None
            self._pending_promotion = EvidencePromotion(
                snapshot=snapshot,
                query=query,
                validated_ms=validated_ms,
            )
            self.metrics.evidence_revalidations += 1
            await self._emit(
                {
                    "type": "retrieval.revalidated",
                    "revision": snapshot.revision,
                    "retrieval_id": retrieval_id,
                    "query": query,
                    "state": "in_flight",
                    "from_revision": attempt["source_revision"] if attempt else None,
                    "to_revision": snapshot.revision,
                    "reason": "controller_confirmed",
                    "actor": "trigger_model",
                }
            )

    def _start_retrieval(
        self,
        snapshot: InputSnapshot,
        query: str,
        *,
        controller_validated: bool = True,
        commit_safe_exact: bool = False,
        origin: str = "trigger",
    ) -> str | None:
        if self._active(self._retrieval_task):
            if self._retrieval_query == query and self._compatible_with_latest(snapshot):
                self._note(
                    {
                        "type": "retrieval.kept",
                        "retrieval_id": self._active_retrieval_id,
                        "query": query,
                        "reason": "same_query_compatible",
                    }
                )
                return self._active_retrieval_id
        self._retrieval_count += 1
        retrieval_id = f"retrieval-{self._retrieval_count}"
        if self._active(self._retrieval_task):
            # A model-issued replacement query supersedes the old retrieval. An
            # ordinary revision by itself never reaches this cancellation path.
            self._cancel_retrieval_task(
                self._retrieval_task,
                reason="replacement_query",
                replacement_retrieval_id=retrieval_id,
            )
        if self._pending_promotion and not self._same_query(self._pending_promotion.query, query):
            self._pending_promotion = None
        if self.evidence is not None and not self._same_query(self.evidence.query, query):
            self._note(
                {
                    "type": "evidence.invalidated",
                    "evidence_id": self.evidence.retrieval_id,
                    "query": self.evidence.query,
                    "reason": "replacement_query",
                }
            )
            self.evidence = None
        self._register_attempt(
            retrieval_id,
            origin=origin,
            query=query,
            source_revision=snapshot.revision,
            controller_validated=controller_validated,
            commit_safe_exact=commit_safe_exact,
        )
        self._retrieval_query = query
        self._retrieval_source_text = snapshot.text
        self._retrieval_controller_validated = controller_validated
        self._retrieval_commit_safe_exact = commit_safe_exact
        self._retrieval_task = asyncio.create_task(
            self._retrieval_worker(
                snapshot,
                query,
                controller_validated,
                commit_safe_exact,
                retrieval_id,
                origin,
            )
        )
        self._retrieval_ids[self._retrieval_task] = retrieval_id
        return retrieval_id

    async def _retrieval_worker(
        self,
        snapshot: InputSnapshot,
        query: str,
        controller_validated: bool,
        commit_safe_exact: bool,
        retrieval_id: str,
        origin: str,
    ) -> None:
        try:
            await self._run_retrieval(
                snapshot,
                query,
                controller_validated,
                commit_safe_exact,
                retrieval_id,
                origin,
            )
        finally:
            self._retrieval_ids.pop(asyncio.current_task(), None)
            if self._retrieval_task is asyncio.current_task():
                self._retrieval_task = None
                self._retrieval_query = None
                self._retrieval_source_text = None
                self._retrieval_controller_validated = True
                self._retrieval_commit_safe_exact = False
                self._pending_promotion = None

    async def _run_retrieval(
        self,
        snapshot: InputSnapshot,
        query: str,
        controller_validated: bool,
        commit_safe_exact: bool,
        retrieval_id: str,
        origin: str,
    ) -> None:
        self.metrics.retrieval_calls += 1
        if not controller_validated:
            self.metrics.raw_retrieval_calls += 1
        started = time.perf_counter() * 1000
        if self.metrics.first_retrieval_started_ms is None:
            self.metrics.first_retrieval_started_ms = started
        attempt = self._attempts_by_id.get(retrieval_id)
        if attempt is not None:
            attempt["search_started_ms"] = started
        await self._emit(
            {
                "type": "retrieval.started",
                "revision": snapshot.revision,
                "retrieval_id": retrieval_id,
                "query": query,
                "origin": origin,
                "candidate": not (controller_validated or commit_safe_exact),
                "commit_safe_exact": commit_safe_exact,
            }
        )
        try:
            result = await asyncio.wait_for(
                self.index.search(query, cache_scope=self.cache_scope),
                timeout=self.settings.retrieval_timeout_s,
            )
        except asyncio.CancelledError:
            self._finish_attempt(retrieval_id, "cancelled")
            return
        except TimeoutError:
            if self.previous_query == query:
                self.previous_query = None
            self.metrics.retrieval_timeouts += 1
            self._finish_attempt(retrieval_id, "timeout")
            await self._emit(
                {
                    "type": "retrieval.error",
                    "revision": snapshot.revision,
                    "retrieval_id": retrieval_id,
                    "reason": "timeout",
                }
            )
            return
        except Exception:
            if self.previous_query == query:
                self.previous_query = None
            self.metrics.retrieval_failures += 1
            self._finish_attempt(retrieval_id, "failure")
            await self._emit(
                {
                    "type": "retrieval.error",
                    "revision": snapshot.revision,
                    "retrieval_id": retrieval_id,
                    "reason": "failure",
                }
            )
            return
        self.metrics.retrieval_embedding_tokens += result.embedding_tokens
        self.metrics.retrieval_query_vector_ms += result.query_vector_ms
        self.metrics.retrieval_ann_ms += result.ann_ms
        # Identity protects against a provider that completes after cancellation;
        # prefix compatibility preserves useful work across append-only revisions.
        latest_valid = (
            self._retrieval_task is asyncio.current_task()
            and self._compatible_with_latest(snapshot)
        )
        if not latest_valid:
            self.metrics.stale_discards += 1
            reason = (
                "task_superseded"
                if self._retrieval_task is not asyncio.current_task()
                else "snapshot_incompatible"
            )
            self._finish_attempt(retrieval_id, "stale", reason=reason)
            await self._emit(
                {
                    "type": "retrieval.discarded",
                    "revision": snapshot.revision,
                    "retrieval_id": retrieval_id,
                    "reason": reason,
                }
            )
            return
        completed = time.perf_counter() * 1000
        self._finish_attempt(
            retrieval_id,
            "ready",
            cache_hit=result.cache_hit,
            query_vector_cache_hit=result.query_vector_cache_hit,
            search_cache_age_ms=result.search_cache_age_ms,
            query_vector_ms=result.query_vector_ms,
            ann_ms=result.ann_ms,
            embedding_tokens=result.embedding_tokens,
        )
        if self.metrics.first_retrieval_ready_ms is None:
            self.metrics.first_retrieval_ready_ms = completed
        promotion = self._pending_promotion
        promoted = (
            promotion is not None
            and self._same_query(promotion.query, query)
            and promotion.snapshot.text.startswith(snapshot.text)
            and self._compatible_with_latest(promotion.snapshot)
        )
        accepted_snapshot = promotion.snapshot if promoted and promotion else snapshot
        effective_commit_safe_exact = commit_safe_exact or (
            self._retrieval_task is asyncio.current_task() and self._retrieval_commit_safe_exact
        )
        self.evidence = RetrievalEvidence(
            source_text=accepted_snapshot.text,
            revision=accepted_snapshot.revision,
            query=query,
            result=result,
            started_ms=started,
            completed_ms=completed,
            validated_ms=(
                max(completed, promotion.validated_ms)
                if promoted and promotion is not None
                else None
            ),
            controller_validated=(
                controller_validated or (promoted and not effective_commit_safe_exact)
            ),
            commit_safe_exact=effective_commit_safe_exact,
            retrieval_id=retrieval_id,
            origin=origin,
        )
        await self._emit(
            {
                "type": "retrieval.ready",
                "revision": accepted_snapshot.revision,
                "retrieval_id": retrieval_id,
                "query": query,
                "origin": origin,
                "hits": len(result.hits),
                "elapsed_ms": round(result.elapsed_ms, 2),
                # Named search_cache_hit so the benchmark's nested cache_hit
                # aggregation keeps its pre-existing accepted-retrieval scope.
                "search_cache_hit": result.cache_hit,
                "candidate": not (controller_validated or promoted or effective_commit_safe_exact),
                "commit_safe_exact": effective_commit_safe_exact,
            }
        )

    def _exact_evidence(self, snapshot: InputSnapshot) -> RetrievalEvidence | None:
        evidence = self.evidence
        if (
            evidence is not None
            and (evidence.controller_validated or evidence.commit_safe_exact)
            and evidence.source_text == snapshot.text
        ):
            return evidence
        return None

    def _validation_candidate_query(self, snapshot: InputSnapshot) -> str | None:
        if self.previous_query:
            return self.previous_query
        if (
            self._active(self._retrieval_task)
            and self._retrieval_query is not None
            and self._retrieval_source_text is not None
            and snapshot.text.startswith(self._retrieval_source_text)
        ):
            return self._retrieval_query
        evidence = self.evidence
        if evidence is not None and snapshot.text.startswith(evidence.source_text):
            return evidence.query
        return None

    async def _accept(
        self,
        evidence: RetrievalEvidence,
        *,
        from_fallback: bool,
    ) -> RetrievalEvidence:
        self.evidence = evidence
        self.metrics.accepted_retrieval_started_ms = evidence.started_ms
        self.metrics.accepted_retrieval_completed_ms = evidence.completed_ms
        self.metrics.accepted_retrieval_ready_ms = evidence.validated_ms or evidence.completed_ms
        self.metrics.accepted_revision = evidence.revision
        self.metrics.accepted_from_fallback = from_fallback
        self.metrics.accepted_origin = evidence.origin
        self.metrics.accepted_query = evidence.query
        effective_ready_ms = evidence.validated_ms or evidence.completed_ms
        self.metrics.accepted_ready_before_commit = bool(
            self.metrics.commit_ms is not None and effective_ready_ms <= self.metrics.commit_ms
        )
        if not from_fallback:
            self.metrics.evidence_reuses += 1
            await self._emit(
                {
                    "type": "retrieval.reused",
                    "revision": evidence.revision,
                    "retrieval_id": evidence.retrieval_id,
                    "query": evidence.query,
                    "origin": evidence.origin,
                    "ready_before_commit": self.metrics.accepted_ready_before_commit,
                    "retrieval_completed_before_commit": bool(
                        self.metrics.commit_ms is not None
                        and evidence.completed_ms <= self.metrics.commit_ms
                    ),
                }
            )
        return evidence

    async def _direct_commit_retrieval(
        self,
        snapshot: InputSnapshot,
        retrieval_id: str,
    ) -> RetrievalEvidence:
        """Search the immutable committed text once without another model decision."""
        retrieval_query = bounded_retrieval_query(snapshot.text)
        self.metrics.retrieval_calls += 1
        started = time.perf_counter() * 1000
        attempt = self._register_attempt(
            retrieval_id,
            origin="direct_commit",
            query=retrieval_query,
            source_revision=snapshot.revision,
            controller_validated=True,
            commit_safe_exact=True,
        )
        attempt["search_started_ms"] = started
        try:
            result = await asyncio.wait_for(
                self.index.search(retrieval_query, cache_scope=self.cache_scope),
                timeout=self.settings.retrieval_timeout_s,
            )
        except TimeoutError:
            self.metrics.retrieval_timeouts += 1
            self._finish_attempt(retrieval_id, "timeout")
            raise RuntimeError("committed-text retrieval timed out") from None
        except Exception:
            self.metrics.retrieval_failures += 1
            self._finish_attempt(retrieval_id, "failure")
            raise
        self.metrics.retrieval_embedding_tokens += result.embedding_tokens
        self.metrics.retrieval_query_vector_ms += result.query_vector_ms
        self.metrics.retrieval_ann_ms += result.ann_ms
        completed = time.perf_counter() * 1000
        self._finish_attempt(
            retrieval_id,
            "ready",
            cache_hit=result.cache_hit,
            query_vector_cache_hit=result.query_vector_cache_hit,
            search_cache_age_ms=result.search_cache_age_ms,
            query_vector_ms=result.query_vector_ms,
            ann_ms=result.ann_ms,
            embedding_tokens=result.embedding_tokens,
        )
        return RetrievalEvidence(
            source_text=snapshot.text,
            revision=snapshot.revision,
            query=result.query,
            result=result,
            started_ms=started,
            completed_ms=completed,
            retrieval_id=retrieval_id,
            origin="direct_commit",
        )

    async def _stop_remaining_speculation(self) -> None:
        """Cancel unfinished work after exact committed evidence has won."""
        cancelled = False
        if self._active(self._trigger_task):
            task = self._trigger_task
            self._cancel_trigger_task(task, reason="accepted_evidence_won")
            self._trigger_task = None
            self._track_cleanup(task)
            cancelled = True
        if self._active(self._retrieval_task):
            cancelled = (
                self._cancel_retrieval_task(
                    self._retrieval_task,
                    reason="accepted_evidence_won",
                )
                or cancelled
            )
        self._pending_snapshot = None
        self._pending_promotion = None
        if cancelled:
            await asyncio.sleep(0)

    async def _stop_trigger_for_commit(self) -> None:
        """Stop an unfinished prefix decision while preserving useful retrieval."""
        self._pending_snapshot = None
        if not self._active(self._trigger_task):
            return
        task = self._trigger_task
        self._cancel_trigger_task(task, reason="commit_fallback")
        self._trigger_task = None
        self._track_cleanup(task)
        await asyncio.sleep(0)

    def _track_cleanup(self, task: asyncio.Task) -> None:
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._finish_cleanup)

    def _finish_cleanup(self, task: asyncio.Task) -> None:
        self._cleanup_tasks.discard(task)
        self._retrieval_cancel_requested.discard(task)
        self._retrieval_ids.pop(task, None)
        self._trigger_ids.pop(task, None)
        try:
            task.result()
        except asyncio.CancelledError, Exception:
            pass

    def _set_trigger_call_state(self, state: str) -> None:
        task = asyncio.current_task()
        if task in self._trigger_call_states:
            self._trigger_call_states[task] = state

    def _cancel_trigger_task(self, task: asyncio.Task, *, reason: str = "superseded") -> None:
        state = self._trigger_call_states.get(task)
        if state == "cancel_requested":
            return
        self.metrics.trigger_cancellations += 1
        if state == "pending":
            self.metrics.unpriced_trigger_cancellations += 1
        self._trigger_call_states[task] = "cancel_requested"
        self._note(
            {
                "type": "trigger.cancelled",
                "trigger_id": self._trigger_ids.get(task, "trigger-?"),
                "reason": reason,
            }
        )
        task.cancel()

    def _cancel_retrieval_task(
        self,
        task: asyncio.Task,
        *,
        reason: str = "superseded",
        replacement_retrieval_id: str | None = None,
    ) -> bool:
        """Detach and request cancellation once for one retrieval task."""
        if task.done() or task in self._retrieval_cancel_requested:
            return False
        self._retrieval_cancel_requested.add(task)
        self.metrics.retrieval_cancellations += 1
        retrieval_id = self._retrieval_ids.get(task)
        attempt = self._attempts_by_id.get(retrieval_id) if retrieval_id else None
        self._finish_attempt(retrieval_id, "cancelled", reason=reason)
        cancelled_event = {
            "type": "retrieval.cancelled",
            "retrieval_id": retrieval_id or "retrieval-?",
            "query": attempt["query"] if attempt else self._retrieval_query,
            "reason": reason,
        }
        if replacement_retrieval_id is not None:
            cancelled_event["replacement_retrieval_id"] = replacement_retrieval_id
        self._note(cancelled_event)
        if self._retrieval_task is task:
            self._retrieval_task = None
            self._retrieval_query = None
            self._retrieval_source_text = None
            self._retrieval_controller_validated = True
            self._retrieval_commit_safe_exact = False
            self._pending_promotion = None
        task.cancel()
        self._track_cleanup(task)
        return True

    async def _cancel_active_retrieval(self, reason: str = "superseded") -> None:
        self._pending_promotion = None
        if not self._active(self._retrieval_task):
            return
        task = self._retrieval_task
        if self._cancel_retrieval_task(task, reason=reason):
            await asyncio.sleep(0)

    async def _promote_exact_commit_work(
        self,
        snapshot: InputSnapshot,
    ) -> RetrievalEvidence | None:
        """Promote work only when its source text exactly matches Send."""
        evidence = self.evidence
        if (
            evidence is not None
            and (evidence.controller_validated or evidence.commit_safe_exact)
            and evidence.source_text == snapshot.text
        ):
            return self._exact_evidence(snapshot)

        retrieval_task = self._retrieval_task
        retrieval_query = self._retrieval_query
        retrieval_source = self._retrieval_source_text
        promotion = self._pending_promotion
        validation_text = (
            promotion.snapshot.text
            if promotion is not None
            and retrieval_query is not None
            and self._same_query(promotion.query, retrieval_query)
            else retrieval_source
            if self._retrieval_controller_validated or self._retrieval_commit_safe_exact
            else None
        )
        if (
            self._active(retrieval_task)
            and retrieval_query is not None
            and validation_text is not None
            and validation_text == snapshot.text
        ):
            retrieval_id = self._retrieval_ids.get(retrieval_task)
            attempt = self._attempts_by_id.get(retrieval_id) if retrieval_id else None
            self._pending_promotion = EvidencePromotion(
                snapshot=snapshot,
                query=retrieval_query,
                validated_ms=time.perf_counter() * 1000,
            )
            self.metrics.evidence_revalidations += 1
            await self._emit(
                {
                    "type": "retrieval.revalidated",
                    "revision": snapshot.revision,
                    "retrieval_id": retrieval_id,
                    "query": retrieval_query,
                    "state": "exact_in_flight_at_commit",
                    "to_revision": snapshot.revision,
                    "reason": "exact_text_match_at_commit",
                }
            )
            wait_started = time.perf_counter() * 1000
            await asyncio.shield(retrieval_task)
            wait_completed = time.perf_counter() * 1000
            exact = self._exact_evidence(snapshot)
            self.metrics.inflight_wait = {
                "retrieval_id": retrieval_id,
                "origin": attempt["origin"] if attempt else None,
                "age_at_commit_ms": (
                    wait_started - attempt["created_ms"] if attempt else None
                ),
                "shield_wait_ms": wait_completed - wait_started,
                "evidence_available_after_wait": exact is not None,
            }
            return exact
        return None

    async def _commit_with_exact_fallback(
        self,
        snapshot: InputSnapshot,
    ) -> RetrievalEvidence:
        """Search the immutable committed text once when speculation cannot be reused."""
        cancel_started = time.perf_counter() * 1000
        await self._stop_trigger_for_commit()
        await self._cancel_active_retrieval(reason="commit_fallback")
        cancel_completed = time.perf_counter() * 1000
        self.metrics.commit_fallbacks += 1
        self._retrieval_count += 1
        retrieval_id = f"retrieval-{self._retrieval_count}"
        await self._emit(
            {
                "type": "retrieval.fallback",
                "revision": snapshot.revision,
                "retrieval_id": retrieval_id,
                "query": snapshot.text,
                "search_query": bounded_retrieval_query(snapshot.text),
                "reason": self.metrics.fallback_reason or "no_exact_evidence_at_commit",
            }
        )
        search_started = time.perf_counter() * 1000
        evidence = await self._direct_commit_retrieval(snapshot, retrieval_id)
        state = self.metrics.state_at_commit
        if state is not None:
            state["fallback_cancel_ms"] = cancel_completed - cancel_started
            state["fallback_search_ms"] = time.perf_counter() * 1000 - search_started
        return await self._accept(evidence, from_fallback=True)

    async def commit(self, snapshot: InputSnapshot) -> RetrievalEvidence:
        self.freeze_at_commit(snapshot)

        # Do not wait behind speculative work when the exact committed revision
        # already has accepted evidence.
        exact = self._exact_evidence(snapshot)
        if exact is not None:
            self.metrics.commit_branch = "exact_evidence"
            await self._stop_remaining_speculation()
            return await self._accept(exact, from_fallback=False)

        exact = await self._promote_exact_commit_work(snapshot)
        if exact is not None:
            self.metrics.commit_branch = "inflight_wait_promoted"
            await self._stop_remaining_speculation()
            return await self._accept(exact, from_fallback=False)

        self.metrics.commit_branch = "fallback"
        self.metrics.fallback_reason = self._classify_fallback_reason(snapshot)
        return await self._commit_with_exact_fallback(snapshot)

    def _classify_fallback_reason(self, snapshot: InputSnapshot) -> str:
        """Name why commit could not reuse speculation, for the durable record."""
        state = self.metrics.state_at_commit or {}
        if state.get("correction_at_commit"):
            return "non_append_correction_at_commit"
        evidence = self.evidence
        if evidence is not None:
            if evidence.source_text != snapshot.text:
                return "source_text_mismatch"
            return "unvalidated_evidence"
        if self._active(self._retrieval_task):
            return "inflight_not_exact"
        if self.metrics.retrieval_timeouts:
            return "prior_retrieval_timeout"
        if self.metrics.retrieval_failures:
            return "prior_retrieval_failure"
        if self.metrics.trigger_timeouts:
            return "trigger_timeouts"
        if self.metrics.trigger_calls == 0:
            return "no_speculation"
        return "no_evidence"

    async def close(self) -> None:
        self._closed = True
        self._pending_snapshot = None
        self._pending_promotion = None
        tasks = {
            task
            for task in (
                self._trigger_task,
                self._retrieval_task,
                self._quiet_task,
                *self._cleanup_tasks,
            )
            if task is not None
        }
        for task in tasks:
            if task and not task.done() and task not in self._retrieval_cancel_requested:
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
