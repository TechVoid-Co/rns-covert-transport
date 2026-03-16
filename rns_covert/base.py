"""
Base class for all covert transport interfaces.

Handles the Reticulum interface contract, HDLC framing,
fixed-size padding (anti-DPI), packet batching, send rate
limiting, poll loops, and error recovery.

Send and receive are fully independent: the flush loop (SMTP)
and poll loop (IMAP) run on separate threads with separate
error counters and connection lifecycles.

Subclasses only need to implement:
  - start_transport()
  - send_packet(encoded_data: bytes)
  - poll_packets() -> list[bytes]
  - stop_transport()
"""

import base64
import collections
import os
import struct
import threading
import time

import RNS
from RNS.Interfaces.Interface import Interface


class HDLC:
    """Simplified HDLC framing, matching Reticulum's PipeInterface."""
    FLAG     = 0x7E
    ESC      = 0x7D
    ESC_MASK = 0x20

    @staticmethod
    def escape(data: bytes) -> bytes:
        data = data.replace(bytes([HDLC.ESC]), bytes([HDLC.ESC, HDLC.ESC ^ HDLC.ESC_MASK]))
        data = data.replace(bytes([HDLC.FLAG]), bytes([HDLC.ESC, HDLC.FLAG ^ HDLC.ESC_MASK]))
        return data

    @staticmethod
    def frame(data: bytes) -> bytes:
        """Wrap data in HDLC flags with escaping."""
        return bytes([HDLC.FLAG]) + HDLC.escape(data) + bytes([HDLC.FLAG])

    @staticmethod
    def deframe(raw: bytes) -> list:
        """Extract all complete HDLC frames from raw bytes."""
        frames = []
        in_frame = False
        escape = False
        buf = b""

        for byte in raw:
            if in_frame and byte == HDLC.FLAG:
                in_frame = False
                if buf:
                    frames.append(buf)
                buf = b""
            elif byte == HDLC.FLAG:
                in_frame = True
                buf = b""
            elif in_frame:
                if byte == HDLC.ESC:
                    escape = True
                else:
                    if escape:
                        if byte == HDLC.FLAG ^ HDLC.ESC_MASK:
                            byte = HDLC.FLAG
                        elif byte == HDLC.ESC ^ HDLC.ESC_MASK:
                            byte = HDLC.ESC
                        escape = False
                    buf += bytes([byte])

        return frames


class Padding:
    """
    Fixed-size packet padding for DPI resistance.

    Wire format (pre-base85):
        [2 bytes: big-endian actual data length]
        [N bytes: actual data (HDLC-framed packets)]
        [P bytes: random padding to fill to inner_size]
    """

    HEADER_SIZE = 2

    @staticmethod
    def pad(data: bytes, target_size: int) -> bytes:
        actual_len = len(data)
        max_data = target_size - Padding.HEADER_SIZE

        if actual_len > max_data:
            raise ValueError(
                f"Data too large for padding: {actual_len} bytes, "
                f"max is {max_data} (target_size={target_size})"
            )

        header = struct.pack("!H", actual_len)
        fill_len = max_data - actual_len
        fill = os.urandom(fill_len)

        return header + data + fill

    @staticmethod
    def unpad(padded: bytes) -> bytes:
        if len(padded) < Padding.HEADER_SIZE:
            raise ValueError("Padded data too short")

        actual_len = struct.unpack("!H", padded[:Padding.HEADER_SIZE])[0]

        if actual_len > len(padded) - Padding.HEADER_SIZE:
            raise ValueError(
                f"Length header says {actual_len} bytes but only "
                f"{len(padded) - Padding.HEADER_SIZE} available"
            )

        return padded[Padding.HEADER_SIZE : Padding.HEADER_SIZE + actual_len]

    @staticmethod
    def max_payload(target_size: int) -> int:
        return target_size - Padding.HEADER_SIZE

    @staticmethod
    def calculate_hw_mtu(inner_size: int) -> int:
        """
        Max raw Reticulum packet guaranteed to fit after HDLC + padding.
        HDLC worst case: 2 * raw_len + 2
        Must fit in inner_size - 2 (padding header)
        So: raw_len <= (inner_size - 4) / 2
        """
        return (inner_size - 4) // 2

    @staticmethod
    def encoded_output_size(inner_size: int) -> int:
        return len(base64.b85encode(b'\x00' * inner_size))


class CovertInterface(Interface):
    """
    Abstract base for covert Reticulum transports.

    Packet flow:

        Reticulum -> process_outgoing -> queue
                                           |
                              [flush_event or batch_window timer]
                                           |
                              batch HDLC frames -> pad -> send_packet
                                                              |
                                                         [service]
                                                              |
        Reticulum <- process_incoming <- HDLC deframe <- unpad <- poll_packets

    Features:
      - Packet batching: multiple Reticulum packets in one email
      - Rate limiting: configurable max sends per hour
      - Fixed-size padding: every email identical size
      - Idle silence: nothing queued = nothing sent
      - Event-driven flush: first packet triggers immediate send

    Config:
        inner_size = 1280          # Padded payload size (both peers must match)
        max_sends_per_hour = 30    # Rate limit (default: 1 email per 2 min)
        batch_window = 5           # Seconds to collect packets before sending
        poll_interval = 30         # Seconds between inbox checks
    """

    BITRATE_GUESS    = 1000
    DEFAULT_IFAC_SIZE = 8
    DEFAULT_POLL_INTERVAL = 30
    DEFAULT_RETRY_DELAY   = 60
    MAX_CONSECUTIVE_ERRORS = 5
    DEFAULT_INNER_SIZE = 1280
    DEFAULT_MAX_SENDS_PER_HOUR = 30
    DEFAULT_BATCH_WINDOW = 5
    DEFAULT_MAX_QUEUE_SIZE = 1000

    def __init__(self, owner, configuration):
        super().__init__()

        c = Interface.get_config_obj(configuration)

        self.name     = c["name"]
        self.owner    = owner
        self.online   = False
        self.bitrate  = int(c.get("bitrate", self.BITRATE_GUESS))

        # Fixed-size padding
        self.inner_size = int(c.get("inner_size", self.DEFAULT_INNER_SIZE))
        self.HW_MTU = Padding.calculate_hw_mtu(self.inner_size)

        if self.HW_MTU < 64:
            raise ValueError(
                f"inner_size={self.inner_size} too small -- HW_MTU={self.HW_MTU}. "
                f"Use inner_size >= 132."
            )

        self.encoded_size = Padding.encoded_output_size(self.inner_size)

        # Rate limiting
        self.max_sends_per_hour = int(c.get("max_sends_per_hour", self.DEFAULT_MAX_SENDS_PER_HOUR))
        self._min_send_interval = 3600.0 / self.max_sends_per_hour if self.max_sends_per_hour > 0 else 0
        self._last_send_time = 0

        # Batching
        self.batch_window = float(c.get("batch_window", self.DEFAULT_BATCH_WINDOW))
        self._outgoing_queue = collections.deque(maxlen=self.DEFAULT_MAX_QUEUE_SIZE)
        self._queue_lock = threading.Lock()

        RNS.log(
            f"{self}: inner_size={self.inner_size}, HW_MTU={self.HW_MTU}, "
            f"rate={self.max_sends_per_hour}/hr, batch_window={self.batch_window}s",
            RNS.LOG_VERBOSE,
        )

        # Polling
        self.poll_interval = float(c.get("poll_interval", self.DEFAULT_POLL_INTERVAL))
        self.retry_delay   = float(c.get("retry_delay", self.DEFAULT_RETRY_DELAY))
        self.drop_on_fail  = c.get("drop_on_fail", "no").lower() in ("yes", "true", "1")
        self._config = c

        # Threads and state
        self._poll_thread   = None
        self._flush_thread  = None
        self._stop_event    = threading.Event()
        self._shutdown_event = threading.Event()
        self._flush_event   = threading.Event()
        self._poll_error_count  = 0
        self._flush_error_count = 0
        self._lock          = threading.Lock()
        self._reconnecting  = False

        # Start up
        try:
            RNS.log(f"Starting covert transport for {self}...", RNS.LOG_VERBOSE)
            self.start_transport()
            self.online = True
            self._start_poll_loop()
            self._start_flush_loop()
            RNS.log(f"Covert transport {self} is now online", RNS.LOG_VERBOSE)

        except Exception as e:
            RNS.log(f"Could not start covert transport {self}: {e}", RNS.LOG_ERROR)
            self.online = False
            self._schedule_reconnect()

    # ------------------------------------------------------------------
    #  Subclasses must implement these
    # ------------------------------------------------------------------

    def start_transport(self):
        raise NotImplementedError

    def send_packet(self, encoded_data: bytes):
        raise NotImplementedError

    def poll_packets(self) -> list:
        raise NotImplementedError

    def stop_transport(self):
        pass

    # ------------------------------------------------------------------
    #  Encoding pipeline
    # ------------------------------------------------------------------

    def encode_payload(self, raw_packet: bytes) -> bytes:
        """Single packet: HDLC frame -> pad -> base85."""
        framed = HDLC.frame(raw_packet)
        padded = Padding.pad(framed, self.inner_size)
        return base64.b85encode(padded)

    def encode_batch(self, raw_packets: list) -> list:
        """
        Batch multiple packets into as few padded payloads as possible.

        HDLC frames are concatenated. Multiple frames that fit within
        one inner_size go in one email. If they overflow, they spill
        into additional emails.

        Returns a list of encoded payloads (each exactly encoded_size).
        """
        if not raw_packets:
            return []

        # Frame all packets
        frames = [HDLC.frame(pkt) for pkt in raw_packets]
        max_data = Padding.max_payload(self.inner_size)

        # Bin-pack frames into payloads
        payloads = []
        current_bin = b""

        for frame in frames:
            if len(current_bin) + len(frame) <= max_data:
                current_bin += frame
            else:
                # Current bin is full -- pad and emit
                if current_bin:
                    padded = Padding.pad(current_bin, self.inner_size)
                    payloads.append(base64.b85encode(padded))
                current_bin = frame

        # Flush remaining
        if current_bin:
            padded = Padding.pad(current_bin, self.inner_size)
            payloads.append(base64.b85encode(padded))

        return payloads

    def decode_payload(self, encoded: bytes) -> list:
        """Decode a payload (possibly containing batched packets)."""
        try:
            padded = base64.b85decode(encoded)
            framed = Padding.unpad(padded)
            return HDLC.deframe(framed)
        except Exception as e:
            RNS.log(f"Decode error on {self}: {e}", RNS.LOG_DEBUG)
            return []

    # ------------------------------------------------------------------
    #  Reticulum interface contract
    # ------------------------------------------------------------------

    def process_outgoing(self, data: bytes):
        """
        Called by Reticulum when it wants to send a packet.
        Queues the packet for batched, rate-limited sending.
        Wakes the flush loop immediately on first packet.
        """
        if not self.online:
            return

        with self._queue_lock:
            was_empty = len(self._outgoing_queue) == 0
            if len(self._outgoing_queue) >= self._outgoing_queue.maxlen:
                RNS.log(f"{self}: outgoing queue full, dropping oldest packet", RNS.LOG_WARNING)
            self._outgoing_queue.append(data)
            self.txb += len(data)

        if was_empty:
            self._flush_event.set()

    def process_incoming(self, data: bytes):
        """Pass a received raw packet up to Reticulum."""
        self.rxb += len(data)
        self.owner.inbound(data, self)

    # ------------------------------------------------------------------
    #  Flush loop (batching + rate limiting)
    # ------------------------------------------------------------------

    def _start_flush_loop(self):
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._flush_thread.start()

    def _flush_loop(self):
        """
        Flush the outgoing queue. Wakes immediately when a packet
        arrives in an empty queue, or after batch_window timeout.
        """
        while not self._stop_event.is_set():
            self._flush_event.wait(timeout=self.batch_window)
            self._flush_event.clear()

            if not self.online or self._stop_event.is_set():
                continue

            # Drain the queue
            packets = []
            with self._queue_lock:
                while self._outgoing_queue:
                    packets.append(self._outgoing_queue.popleft())

            if not packets:
                continue

            # Encode into batched payloads
            sent_count = 0
            try:
                payloads = self.encode_batch(packets)

                for payload in payloads:
                    self._wait_for_rate_limit()

                    with self._lock:
                        self.send_packet(payload)
                    sent_count += 1
                    self._last_send_time = time.time()

                with self._lock:
                    self._flush_error_count = 0

                RNS.log(
                    f"{self}: sent batch ({len(packets)} pkt(s) in "
                    f"{len(payloads)} payload(s))",
                    RNS.LOG_DEBUG,
                )

            except Exception as e:
                smtp_code = getattr(e, 'smtp_code', 0)
                is_permanent = smtp_code >= 500

                if is_permanent and self.drop_on_fail:
                    RNS.log(
                        f"{self}: server rejected ({smtp_code}), "
                        f"dropping {len(packets)} pkt(s): {e}",
                        RNS.LOG_ERROR,
                    )
                elif is_permanent:
                    RNS.log(
                        f"{self}: server rejected ({smtp_code}), "
                        f"keeping {len(packets)} pkt(s) in queue to retry: {e}",
                        RNS.LOG_WARNING,
                    )
                    self._handle_flush_error()
                    with self._queue_lock:
                        for pkt in reversed(packets):
                            self._outgoing_queue.appendleft(pkt)
                else:
                    RNS.log(f"Flush error on {self}: {e}", RNS.LOG_WARNING)
                    self._handle_flush_error()
                    if sent_count == 0:
                        with self._queue_lock:
                            for pkt in reversed(packets):
                                self._outgoing_queue.appendleft(pkt)

    def _wait_for_rate_limit(self):
        """Sleep if necessary to stay under max_sends_per_hour."""
        if self._min_send_interval <= 0:
            return

        elapsed = time.time() - self._last_send_time
        if elapsed < self._min_send_interval:
            wait = self._min_send_interval - elapsed
            RNS.log(f"{self}: rate limit, waiting {wait:.1f}s", RNS.LOG_DEBUG)
            self._stop_event.wait(wait)

    # ------------------------------------------------------------------
    #  Poll loop
    # ------------------------------------------------------------------

    def _start_poll_loop(self):
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        while not self._stop_event.is_set():
            if self.online:
                try:
                    payloads = self.poll_packets()

                    for payload in payloads:
                        packets = self.decode_payload(payload)
                        for pkt in packets:
                            self.process_incoming(pkt)

                except Exception as e:
                    RNS.log(f"Poll error on {self}: {e}", RNS.LOG_WARNING)
                    self._handle_poll_error()

            self._stop_event.wait(self.poll_interval)

    # ------------------------------------------------------------------
    #  Error recovery (independent for poll and flush)
    # ------------------------------------------------------------------

    def _handle_flush_error(self):
        with self._lock:
            self._flush_error_count += 1
            count = self._flush_error_count
        if count >= self.MAX_CONSECUTIVE_ERRORS:
            RNS.log(
                f"{self}: {count} consecutive flush errors, going offline.",
                RNS.LOG_ERROR,
            )
            self.online = False
            self._schedule_reconnect()

    def _handle_poll_error(self):
        with self._lock:
            self._poll_error_count += 1
            count = self._poll_error_count
        if count >= self.MAX_CONSECUTIVE_ERRORS:
            RNS.log(
                f"{self}: {count} consecutive poll errors, going offline.",
                RNS.LOG_ERROR,
            )
            self.online = False
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        with self._lock:
            if self._reconnecting:
                return
            self._reconnecting = True

        def _reconnect():
            try:
                self._stop_event.set()
                time.sleep(1)
                self._stop_event.clear()

                while not self.online:
                    if self._shutdown_event.is_set():
                        return
                    self._shutdown_event.wait(self.retry_delay)
                    if self._shutdown_event.is_set():
                        return
                    try:
                        RNS.log(f"Attempting to reconnect {self}...", RNS.LOG_VERBOSE)
                        self.start_transport()
                        self.online = True
                        with self._lock:
                            self._flush_error_count = 0
                            self._poll_error_count = 0
                        self._start_poll_loop()
                        self._start_flush_loop()
                        RNS.log(f"Reconnected {self}", RNS.LOG_NOTICE)
                        return
                    except Exception as e:
                        RNS.log(f"Reconnect failed for {self}: {e}", RNS.LOG_WARNING)
            finally:
                with self._lock:
                    self._reconnecting = False

        t = threading.Thread(target=_reconnect, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    #  Lifecycle
    # ------------------------------------------------------------------

    def detach(self):
        self._shutdown_event.set()
        self._stop_event.set()
        self._flush_event.set()
        self.online = False

        # Final flush -- try to send anything still queued
        packets = []
        with self._queue_lock:
            while self._outgoing_queue:
                packets.append(self._outgoing_queue.popleft())
        if packets:
            try:
                payloads = self.encode_batch(packets)
                for payload in payloads:
                    self.send_packet(payload)
            except Exception as e:
                RNS.log(f"{self}: error during final flush on detach: {e}", RNS.LOG_DEBUG)

        try:
            self.stop_transport()
        except Exception as e:
            RNS.log(f"Error during {self} shutdown: {e}", RNS.LOG_WARNING)

    def __str__(self):
        return f"CovertInterface[{self.name}]"
