"""Private bounded calculation cache; keys contain every loaded input dependency.

This is an in-process optimization, never a replacement for raw attendance data.
Only completed historical employee ranges are cached. Compressed values are
created by this process and are never read from disk or supplied by a client.
"""
from collections import OrderedDict
from hashlib import sha256
import pickle
import threading
import zlib

MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 20000
_LOCK = threading.Lock()
_VALUES = OrderedDict()
_SIZE = 0
_HITS = 0
_MISSES = 0


def digest(value) -> bytes:
    return sha256(pickle.dumps(value, protocol=5)).digest()


def get(key):
    global _HITS, _MISSES
    with _LOCK:
        value = _VALUES.get(key)
        if value is None:
            _MISSES += 1
            return None
        _VALUES.move_to_end(key)
        _HITS += 1
    return pickle.loads(zlib.decompress(value))


def put(key, rows):
    global _SIZE
    value = zlib.compress(pickle.dumps(rows, protocol=5), 1)
    if len(value) > MAX_BYTES:
        return
    with _LOCK:
        old = _VALUES.pop(key, None)
        _SIZE -= len(old) + len(key) + 128 if old is not None else 0
        _VALUES[key] = value
        _SIZE += len(value) + len(key) + 128
        while _SIZE > MAX_BYTES or len(_VALUES) > MAX_ENTRIES:
            old_key, old_value = _VALUES.popitem(last=False)
            _SIZE -= len(old_value) + len(old_key) + 128


def stats():
    with _LOCK:
        return {"entries": len(_VALUES), "compressed_bytes_with_key_allowance": _SIZE,
                "max_bytes": MAX_BYTES, "hits": _HITS, "misses": _MISSES,
                "historical_only": True, "persistent": False}


def clear():
    global _SIZE, _HITS, _MISSES
    with _LOCK:
        _VALUES.clear()
        _SIZE = _HITS = _MISSES = 0
