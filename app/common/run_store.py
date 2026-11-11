"""Redis-backed run state, event log, queue, quarantine and cache.

Queue design (A4): a Redis list used as a reliable queue. The API ``LPUSH``es a job; a worker
atomically ``BLMOVE``s it into its own processing list, and removes it only once the run reached a
terminal state. If a worker dies mid-run its processing list is requeued, either by the same worker
on restart or by any live worker once the dead worker's heartbeat expires (A2 failure handling).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis

from app.contracts.runs import RunRecord

PREFIX = "ecs"
QUEUE_KEY = f"{PREFIX}:queue:runs"
QUARANTINE_KEY = f"{PREFIX}:quarantine"
QUARANTINE_MAX = 1000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class RunStore:
    def __init__(self, client: redis.Redis, ttl_seconds: int) -> None:
        self.r = client
        self.ttl = ttl_seconds

    # ---------- runs ----------
    @staticmethod
    def _run_key(run_id: str) -> str:
        return f"{PREFIX}:run:{run_id}"

    @staticmethod
    def _events_key(run_id: str) -> str:
        return f"{PREFIX}:run:{run_id}:events"

    def submit(self, record: RunRecord, job: dict[str, Any]) -> None:
        """Persist the run and enqueue it atomically: no run without a job, no job without a run."""
        pipe = self.r.pipeline(transaction=True)
        pipe.set(self._run_key(record.run_id), record.model_dump_json(), ex=self.ttl)
        pipe.lpush(QUEUE_KEY, json.dumps(job))
        pipe.execute()

    def get(self, run_id: str) -> RunRecord | None:
        raw = self.r.get(self._run_key(run_id))
        return RunRecord.model_validate_json(raw) if raw else None

    def save(self, record: RunRecord) -> None:
        record.updated_at = utcnow()
        self.r.set(self._run_key(record.run_id), record.model_dump_json(), ex=self.ttl)

    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        key = self._events_key(run_id)
        pipe = self.r.pipeline(transaction=False)
        pipe.rpush(key, json.dumps(event, default=str))
        pipe.expire(key, self.ttl)
        pipe.execute()

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return [json.loads(e) for e in self.r.lrange(self._events_key(run_id), 0, -1)]

    # ---------- queue ----------
    @staticmethod
    def _processing_key(worker_id: str) -> str:
        return f"{PREFIX}:queue:processing:{worker_id}"

    @staticmethod
    def _alive_key(worker_id: str) -> str:
        return f"{PREFIX}:worker:{worker_id}:alive"

    def dequeue(self, worker_id: str, timeout_s: float) -> str | None:
        return self.r.blmove(QUEUE_KEY, self._processing_key(worker_id), timeout_s, "RIGHT", "LEFT")

    def ack(self, worker_id: str, raw_job: str) -> None:
        self.r.lrem(self._processing_key(worker_id), 1, raw_job)

    def requeue_processing(self, worker_id: str) -> int:
        """Move unfinished jobs of ``worker_id`` back to the head of the queue."""
        moved = 0
        while self.r.lmove(self._processing_key(worker_id), QUEUE_KEY, "RIGHT", "RIGHT"):
            moved += 1
        return moved

    def heartbeat(self, worker_id: str, ttl_s: int) -> None:
        self.r.set(self._alive_key(worker_id), utcnow().isoformat(), ex=ttl_s)

    def reap_dead_workers(self) -> int:
        moved = 0
        prefix = f"{PREFIX}:queue:processing:"
        for key in self.r.scan_iter(match=prefix + "*"):
            worker_id = key[len(prefix):]
            if not self.r.exists(self._alive_key(worker_id)):
                moved += self.requeue_processing(worker_id)
        return moved

    def workers_alive(self) -> int:
        """Workers whose heartbeat has not expired (informational; see docs/incident-log.md INC-001)."""
        return sum(1 for _ in self.r.scan_iter(match=f"{PREFIX}:worker:*:alive", count=100))

    def queue_depth(self) -> int:
        return int(self.r.llen(QUEUE_KEY))

    # ---------- whole-run cache (D7) ----------
    @staticmethod
    def cache_key(request: dict[str, Any], version: str) -> str:
        material = {k: v for k, v in request.items() if k not in {"client_reference"}}
        material["product_description"] = " ".join(str(material.get("product_description", "")).lower().split())
        digest = hashlib.sha256((json.dumps(material, sort_keys=True) + "|" + version).encode()).hexdigest()
        return f"{PREFIX}:cache:run:{digest}"

    def cache_get(self, key: str) -> str | None:
        return self.r.get(key)

    def cache_set(self, key: str, value: str, ttl_s: int) -> None:
        self.r.set(key, value, ex=ttl_s)


class QuarantineStore:
    """Rejected ingestion records with their field-level errors (D1)."""

    def __init__(self, client: redis.Redis) -> None:
        self.r = client

    def add(self, entry: dict[str, Any]) -> str:
        quarantine_id = new_id()
        entry = {"quarantine_id": quarantine_id, "received_at": utcnow().isoformat(), **entry}
        pipe = self.r.pipeline(transaction=True)
        pipe.lpush(QUARANTINE_KEY, json.dumps(entry, default=str))
        pipe.ltrim(QUARANTINE_KEY, 0, QUARANTINE_MAX - 1)
        pipe.execute()
        return quarantine_id

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return [json.loads(e) for e in self.r.lrange(QUARANTINE_KEY, 0, limit - 1)]

    def count(self) -> int:
        return int(self.r.llen(QUARANTINE_KEY))


FEEDBACK_KEY = f"{PREFIX}:feedback"


class FeedbackStore:
    """Reviewer feedback on finished runs (L6).

    One entry per (run, client): a hash field ``<run_id>:<client_id>``, so re-rating a run replaces
    the earlier verdict instead of counting twice. Feedback is the long-term quality record, so it
    does not expire with the run (runs expire after ``RUN_TTL_SECONDS``).
    """

    def __init__(self, client: redis.Redis) -> None:
        self.r = client

    def put(self, entry: dict[str, Any]) -> bool:
        """Store ``entry``; return True if it replaced this client's earlier feedback for the run."""
        field = f"{entry['run_id']}:{entry['client_id']}"
        created = self.r.hset(FEEDBACK_KEY, field, json.dumps(entry, default=str))
        return not bool(created)

    def all(self) -> list[dict[str, Any]]:
        return [json.loads(v) for v in self.r.hvals(FEEDBACK_KEY)]

    def for_run(self, run_id: str) -> list[dict[str, Any]]:
        out = []
        for _field, value in self.r.hscan_iter(FEEDBACK_KEY, match=f"{run_id}:*"):
            out.append(json.loads(value))
        return sorted(out, key=lambda e: e["received_at"])


def summarise_feedback(entries: list[dict[str, Any]], recent: int = 10) -> dict[str, Any]:
    """Aggregate feedback entries into totals by prompt version, route and UTC day."""

    def bucket() -> dict[str, Any]:
        return {"total": 0, "up": 0, "down": 0, "satisfaction": None}

    def add(b: dict[str, Any], rating: str) -> None:
        b["total"] += 1
        b[rating] += 1
        b["satisfaction"] = round(b["up"] / b["total"], 4)

    total = bucket()
    by_prompt: dict[str, dict] = {}
    by_route: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    reasons: dict[str, int] = {}
    for e in entries:
        rating = e["rating"]
        add(total, rating)
        add(by_prompt.setdefault(e.get("prompt_version") or "unknown", bucket()), rating)
        add(by_route.setdefault(e.get("route") or "none", bucket()), rating)
        add(by_day.setdefault(str(e["received_at"])[:10], bucket()), rating)
        for reason in e.get("reasons") or []:
            reasons[reason] = reasons.get(reason, 0) + 1
    newest = sorted(entries, key=lambda e: str(e["received_at"]), reverse=True)[:recent]
    return {"total": total, "by_prompt_version": by_prompt, "by_route": by_route,
            "by_day": dict(sorted(by_day.items())), "reasons": reasons, "recent": newest}


class RateLimiter:
    """Fixed one-minute window per client (D7). Cost control against clients looping on the agent."""

    def __init__(self, client: redis.Redis, per_minute: int, clock=None) -> None:
        import time

        self.r = client
        self.per_minute = per_minute
        self.clock = clock or time.time

    def check(self, client_id: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = self.clock()
        window = int(now // 60)
        key = f"{PREFIX}:ratelimit:{client_id}:{window}"
        pipe = self.r.pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, 61)
        count, _ = pipe.execute()
        if int(count) > self.per_minute:
            return False, max(1, 60 - int(now % 60))
        return True, 0
