# Queue Adapter

How work gets enqueued and consumed in Maro, and how to swap backends.

---

## Current State: File-Based Interrupt Queue

The system's primary queue is `memory/interrupts.jsonl` — the `InterruptQueue` in `interrupt.py`. It's polled between agent loop steps and supports four intent classes:

| Intent | Behavior |
|--------|----------|
| `additive` | Append new steps to pending_steps |
| `corrective` | Replace remaining steps (or goal) |
| `priority` | Prepend new steps — jump the queue |
| `stop` | Break loop, status="interrupted" |

Messages arrive from:
- Telegram (primary) — `telegram_listener.py` posts when a loop is running
- CLI (`maro interrupt`) — direct injection
- Background tasks — `background.py` posts completion signals

The legacy shell queue (`enqueue.sh` → `project_task` payloads) is still supported for backward compat with OpenClaw scripts. Format: `type: project_task`, `payload: project=<slug> :: <task text>`.

---

## Adapter Contract

Any queue backend must satisfy the interface `InterruptQueue` actually exposes (`src/interrupt.py`):

```python
class QueueAdapter:
    def post(self, type: str, payload: str, ...) -> Interrupt:
        """Append a work item. Raises on failure."""

    def poll(self) -> List[Interrupt]:
        """Return pending interrupts (oldest first), marking them consumed. Non-blocking."""
```

There is no separate `ack()` — consumption happens in `poll()` (drain semantics). Current implementation: flat JSONL append (post) + full-file read (poll). Sufficient for single-process, single-box operation.

---

## Backend Trade-offs

| Backend | Latency | Concurrency | Durability | Complexity | Good for |
|---------|---------|-------------|------------|------------|----------|
| **File (current)** | ~1ms | Single writer | Git-trackable | Zero | Single box, ≤10 tasks/min |
| **SQLite** | ~1ms | Multi-writer (WAL) | Durable, transactional | Low | Same box, concurrent personas, ≤1000 tasks/min |
| **Redis** | ~0.1ms | Multi-process, multi-box | Optional persistence | Medium | Multi-box coordination, high throughput |
| **Postgres** | ~2ms | Multi-box, ACID | Full | High | Production fleet, auditable |

The current file-based queue works until you're running 5+ concurrent persona spawns or spanning multiple boxes. The natural upgrade path is **SQLite with WAL mode** — same behavior, zero new services, handles concurrent writers cleanly.

---

## Concurrency Today

`background.py` runs features with `max_workers=2` (configurable). The interrupt queue handles concurrent writes via append semantics — last-write-wins for JSONL is safe because interrupts are consumed sequentially by the agent loop anyway.

The loop lock (`memory/loop.lock`) provides PID-verified mutual exclusion for the main loop. Multiple background sub-goals can run concurrently; only the primary `run_agent_loop` call holds the lock.

---

## Multi-Box Coordination (Future — Phase 22+)

When spawning specialist persona swarms across processes or boxes, the file queue breaks. The minimal coordination primitive needed:

```
Shared task table (SQLite or Redis):
  task_id, type, payload, status, owner_pid, created_at, updated_at

Claim operation (atomic):
  UPDATE tasks SET status='running', owner_pid=?
  WHERE status='pending' AND task_id=?

Heartbeat (stale claim recovery):
  tasks where status='running' AND updated_at < now()-60s → reclaim
```

This is ~100 lines of Python and slots cleanly under the existing `InterruptQueue` interface. The interrupt queue becomes a view over the shared task table filtered to the current process.

**Partially realized since this was written:** `src/task_store.py` (file-per-task JSON, fcntl advisory locking, atomic claims, stale-claim recovery, DAG deps) implements the single-box version of this primitive. Multi-box remains future work. Phase history: `docs/history/` (ROADMAP_ARCHIVE).
