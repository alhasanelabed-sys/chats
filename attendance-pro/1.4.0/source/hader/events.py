"""Live events pushed to open pages (Server-Sent Events).

Handlers run in worker threads, so publishing only appends to a thread-safe ring; each
open page's stream picks up what is newer than what it has seen. Kinds:

* ``punch``    new punches arrived (data: sn, count)
* ``device``   a terminal went online/offline or its counters changed (data: sn, state)
* ``commands`` commands were delivered / answered (data: sn)
* ``people``   employees or templates changed
"""
from __future__ import annotations

import threading
import time
from collections import deque

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

_lock = threading.Lock()
_ring: deque = deque(maxlen=1000)
_seq = 0
_gen = 0
_changes: deque = deque(maxlen=1000)


_listeners: list = []


def listen(fn) -> None:
    """fn(kind, data) is called in the publishing thread for every event."""
    _listeners.append(fn)


DATA_KINDS = ("punch", "people")   # events that change attendance results
_COMMIT_EVENTS = "hader.commit_events"


def publish_after_commit(db: Session, kind: str, **data) -> None:
    """Queue a data event for this transaction; rollback/close discards it."""
    transaction = db.get_nested_transaction() or db.get_transaction()
    if transaction is None:
        raise RuntimeError("after-commit publication requires an active transaction")
    db.info.setdefault(_COMMIT_EVENTS, {}).setdefault(transaction, []).append((kind, data))


@sa_event.listens_for(Session, "after_commit")
def _committed(db: Session) -> None:
    transaction = db.get_nested_transaction() or db.get_transaction()
    pending = db.info.get(_COMMIT_EVENTS, {})
    queued = pending.pop(transaction, [])
    if transaction is not None and transaction.parent is not None:
        # A successful savepoint still depends on its enclosing transaction.
        if queued:
            pending.setdefault(transaction.parent, []).extend(queued)
        return
    db.info.pop(_COMMIT_EVENTS, None)
    for kind, data in queued:
        publish(kind, **data)


@sa_event.listens_for(Session, "after_transaction_end")
def _transaction_ended(db: Session, transaction) -> None:
    pending = db.info.get(_COMMIT_EVENTS)
    if pending is not None:
        pending.pop(transaction, None)
        if transaction.parent is None or not pending:
            db.info.pop(_COMMIT_EVENTS, None)


def publish(kind: str, **data) -> None:
    global _seq, _gen
    with _lock:
        _seq += 1
        _ring.append((_seq, time.time(), kind, data))
        if kind in DATA_KINDS:
            _gen += 1
            _changes.append((_gen, data.get("start") if kind == "punch" else None,
                             data.get("end") if kind == "punch" else None))
    for fn in list(_listeners):
        try:
            fn(kind, data)
        except Exception:  # noqa: BLE001 - a listener must never break the publisher
            pass


def since(seq: int) -> tuple[int, list[tuple[int, str, dict]]]:
    with _lock:
        return _seq, [(s, k, d) for s, _t, k, d in _ring if s > seq]


def bump() -> None:
    """Something was saved (a form, an import, an approval): cached results are stale."""
    global _gen
    with _lock:
        _gen += 1
        _changes.append((_gen, None, None))


def affects_since(generation_id: int, start: str, end: str, until: int | None = None) -> bool:
    """Conservative range invalidation, including unknown or evicted changes."""
    with _lock:
        latest = _gen if until is None else min(_gen, until)
        if generation_id >= latest:
            return False
        if not _changes or _changes[0][0] > generation_id + 1:
            return True
        return any(not first or not last or (first <= end and last >= start)
                   for gen, first, last in _changes if generation_id < gen <= latest)


def generation() -> int:
    """Changes whenever attendance data may have changed (new punches, people, a saved change)."""
    with _lock:
        return _gen


def current() -> int:
    with _lock:
        return _seq
