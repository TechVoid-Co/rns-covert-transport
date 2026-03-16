"""Tests for CovertInterface threading, lifecycle, and error recovery."""

import os
import threading
import time
from unittest.mock import MagicMock

import pytest

from rns_covert.base import CovertInterface


class StubInterface(CovertInterface):
    """Minimal concrete implementation for testing."""

    def __init__(self, owner, configuration, fail_start=False, fail_send=False):
        self._fail_start = fail_start
        self._fail_send = fail_send
        self._sent_payloads = []
        self._poll_data = []
        self._poll_lock = threading.Lock()
        self._started = False
        super().__init__(owner, configuration)

    def start_transport(self):
        if self._fail_start:
            raise ConnectionError("start failed")
        self._started = True

    def send_packet(self, encoded_data: bytes):
        if self._fail_send:
            raise ConnectionError("send failed")
        self._sent_payloads.append(encoded_data)

    def poll_packets(self) -> list:
        with self._poll_lock:
            data = list(self._poll_data)
            self._poll_data.clear()
        return data

    def stop_transport(self):
        self._started = False

    def inject_poll_data(self, payloads):
        with self._poll_lock:
            self._poll_data.extend(payloads)

    def __str__(self):
        return "StubInterface[test]"


def _make_config(**overrides):
    cfg = {
        "name": "TestStub",
        "poll_interval": "9999",
        "batch_window": "9999",
        "max_sends_per_hour": "3600",
        "inner_size": "1280",
        "bitrate": "10000",
    }
    cfg.update(overrides)
    return cfg


def _make_owner():
    owner = MagicMock()
    owner.inbound = MagicMock()
    return owner


class TestRateLimiting:
    def test_rate_limit_enforced(self):
        """Sends should be spaced according to max_sends_per_hour."""
        owner = _make_owner()
        cfg = _make_config(max_sends_per_hour="3600")
        iface = StubInterface(owner, cfg)
        try:
            # min_send_interval should be 1 second for 3600/hr
            assert iface._min_send_interval == pytest.approx(1.0)
        finally:
            iface.detach()


class TestBatchQueueing:
    def test_outgoing_queue_bounded(self):
        """Queue should have a maxlen."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            assert iface._outgoing_queue.maxlen == CovertInterface.DEFAULT_MAX_QUEUE_SIZE
        finally:
            iface.detach()

    def test_process_outgoing_queues(self):
        """process_outgoing should add packets to the queue."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            iface.process_outgoing(b"packet1")
            iface.process_outgoing(b"packet2")
            assert len(iface._outgoing_queue) == 2
        finally:
            iface.detach()

    def test_process_outgoing_skipped_when_offline(self):
        """process_outgoing should discard when offline."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            iface.online = False
            iface.process_outgoing(b"packet")
            assert len(iface._outgoing_queue) == 0
        finally:
            iface.detach()


class TestErrorRecovery:
    def test_flush_error_count_increments(self):
        """_handle_flush_error should increment flush error counter."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            iface._handle_flush_error()
            assert iface._flush_error_count == 1
            assert iface.online is True
        finally:
            iface.detach()

    def test_poll_error_count_increments(self):
        """_handle_poll_error should increment poll error counter."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            iface._handle_poll_error()
            assert iface._poll_error_count == 1
            assert iface.online is True
        finally:
            iface.detach()

    def test_goes_offline_after_max_flush_errors(self):
        """After MAX_CONSECUTIVE_ERRORS flush errors, should go offline."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            for _ in range(CovertInterface.MAX_CONSECUTIVE_ERRORS):
                iface._handle_flush_error()
            assert iface.online is False
        finally:
            iface.detach()

    def test_goes_offline_after_max_poll_errors(self):
        """After MAX_CONSECUTIVE_ERRORS poll errors, should go offline."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            for _ in range(CovertInterface.MAX_CONSECUTIVE_ERRORS):
                iface._handle_poll_error()
            assert iface.online is False
        finally:
            iface.detach()

    def test_poll_errors_dont_affect_flush(self):
        """Poll errors should not prevent flush from working."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            # 4 poll errors (below threshold)
            for _ in range(4):
                iface._handle_poll_error()
            assert iface.online is True
            assert iface._flush_error_count == 0
        finally:
            iface.detach()


class TestDetach:
    def test_detach_sets_stop_event(self):
        """detach() should set stop event and go offline."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        iface.detach()

        assert iface._stop_event.is_set()
        assert iface.online is False

    def test_detach_flushes_queue(self):
        """detach() should attempt to send queued packets."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)

        iface.process_outgoing(os.urandom(50))
        iface.detach()

        # Should have attempted to send
        assert len(iface._sent_payloads) > 0
        assert len(iface._outgoing_queue) == 0


class TestEncodeDecode:
    def test_encode_decode_roundtrip(self):
        """encode_payload -> decode_payload should be identity."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            raw = os.urandom(100)
            encoded = iface.encode_payload(raw)
            packets = iface.decode_payload(encoded)
            assert len(packets) == 1
            assert packets[0] == raw
        finally:
            iface.detach()

    def test_batch_encode_decode(self):
        """encode_batch should pack multiple packets."""
        owner = _make_owner()
        cfg = _make_config()
        iface = StubInterface(owner, cfg)
        try:
            raw_packets = [os.urandom(50) for _ in range(5)]
            payloads = iface.encode_batch(raw_packets)

            recovered = []
            for payload in payloads:
                recovered.extend(iface.decode_payload(payload))

            assert recovered == raw_packets
        finally:
            iface.detach()


class TestPartialRequeue:
    def test_no_requeue_after_partial_send(self):
        """If some payloads were sent, don't re-queue (avoids duplicates)."""
        owner = _make_owner()
        cfg = _make_config(batch_window="9999", poll_interval="9999")

        call_count = [0]

        class PartialFailInterface(StubInterface):
            def send_packet(self, encoded_data):
                call_count[0] += 1
                if call_count[0] > 1:
                    raise ConnectionError("fail on second send")
                self._sent_payloads.append(encoded_data)

        iface = PartialFailInterface(owner, cfg)
        try:
            # Queue enough packets for >1 payload
            for _ in range(100):
                iface.process_outgoing(os.urandom(200))

            # Event-driven flush triggers immediately on first packet,
            # give it time to process
            time.sleep(1.0)

            # After partial failure, queue should not have all packets back
            # (some were sent successfully)
            assert len(iface._outgoing_queue) == 0
        finally:
            iface.detach()


class TestConfigValidation:
    def test_inner_size_too_small_raises(self):
        """inner_size that produces HW_MTU < 64 should raise ValueError."""
        owner = _make_owner()
        cfg = _make_config(inner_size="100")
        with pytest.raises(ValueError, match="too small"):
            StubInterface(owner, cfg)

    def test_inner_size_minimum(self):
        """inner_size=132 should be the minimum that works."""
        owner = _make_owner()
        cfg = _make_config(inner_size="132")
        iface = StubInterface(owner, cfg)
        try:
            assert iface.HW_MTU == 64
        finally:
            iface.detach()

    def test_missing_name_raises(self):
        """Config without 'name' should raise."""
        owner = _make_owner()
        cfg = _make_config()
        del cfg["name"]
        with pytest.raises(KeyError):
            StubInterface(owner, cfg)
