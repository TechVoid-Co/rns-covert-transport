"""Tests for utility classes."""

from rns_covert.util import BoundedIdSet


class TestBoundedIdSet:
    def test_add_and_contains(self):
        s = BoundedIdSet(maxlen=10)
        s.add("a")
        s.add("b")
        assert "a" in s
        assert "b" in s
        assert "c" not in s

    def test_len(self):
        s = BoundedIdSet(maxlen=10)
        s.add("x")
        s.add("y")
        assert len(s) == 2

    def test_duplicate_not_counted(self):
        s = BoundedIdSet(maxlen=10)
        s.add("a")
        s.add("a")
        assert len(s) == 1

    def test_eviction_on_overflow(self):
        s = BoundedIdSet(maxlen=3)
        s.add("a")
        s.add("b")
        s.add("c")
        assert len(s) == 3

        s.add("d")
        assert len(s) == 3
        assert "a" not in s
        assert "d" in s
        assert "b" in s
        assert "c" in s

    def test_eviction_order(self):
        s = BoundedIdSet(maxlen=2)
        s.add("first")
        s.add("second")
        s.add("third")
        # 'first' should be evicted
        assert "first" not in s
        assert "second" in s
        assert "third" in s

    def test_large_maxlen(self):
        s = BoundedIdSet(maxlen=10000)
        for i in range(10000):
            s.add(str(i))
        assert len(s) == 10000
        # Adding one more evicts the oldest
        s.add("extra")
        assert len(s) == 10000
        assert "0" not in s
        assert "extra" in s
