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
