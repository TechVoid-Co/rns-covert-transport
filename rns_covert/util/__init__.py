import threading
from collections import deque


class BoundedIdSet:
    """A set with bounded size backed by a deque for eviction order.

    When the set reaches maxlen, the oldest entries are evicted.
    Supports ``in`` checks and ``add`` like a regular set.
    Thread-safe.
    """

    def __init__(self, maxlen=10000):
        self._maxlen = maxlen
        self._set = set()
        self._order = deque()
        self._lock = threading.Lock()

    def add(self, item):
        with self._lock:
            if item in self._set:
                return
            if len(self._set) >= self._maxlen:
                evicted = self._order.popleft()
                self._set.discard(evicted)
            self._set.add(item)
            self._order.append(item)

    def __contains__(self, item):
        with self._lock:
            return item in self._set

    def __len__(self):
        with self._lock:
            return len(self._set)
