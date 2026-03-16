"""
Tests for HDLC framing and the CovertInterface base class.
"""

import os
import pytest
from rns_covert.base import HDLC, CovertInterface, Padding


class TestHDLC:
    """Tests for HDLC frame/deframe."""

    def test_roundtrip_simple(self):
        data = b"hello"
        framed = HDLC.frame(data)
        packets = HDLC.deframe(framed)
        assert len(packets) == 1
        assert packets[0] == data

    def test_roundtrip_binary(self):
        data = bytes(range(256))
        framed = HDLC.frame(data)
        packets = HDLC.deframe(framed)
        assert len(packets) == 1
        assert packets[0] == data

    def test_roundtrip_contains_flag_byte(self):
        """Data containing the HDLC flag byte must survive."""
        data = bytes([HDLC.FLAG, 0x00, HDLC.FLAG, HDLC.ESC, 0xFF])
        framed = HDLC.frame(data)
        packets = HDLC.deframe(framed)
        assert len(packets) == 1
        assert packets[0] == data

    def test_roundtrip_contains_escape_byte(self):
        """Data containing the HDLC escape byte must survive."""
        data = bytes([HDLC.ESC, HDLC.ESC, 0x00, HDLC.ESC])
        framed = HDLC.frame(data)
        packets = HDLC.deframe(framed)
        assert len(packets) == 1
        assert packets[0] == data

    def test_multiple_frames(self):
        """Multiple concatenated frames should all be extracted."""
        data1 = b"packet_one"
        data2 = b"packet_two"
        data3 = b"packet_three"
        combined = HDLC.frame(data1) + HDLC.frame(data2) + HDLC.frame(data3)
        packets = HDLC.deframe(combined)
        assert len(packets) == 3
        assert packets[0] == data1
        assert packets[1] == data2
        assert packets[2] == data3

    def test_empty_data(self):
        """Empty data should produce a frame and deframe back."""
        data = b""
        framed = HDLC.frame(data)
        packets = HDLC.deframe(framed)
        # Empty frame produces empty buffer -- not added
        assert len(packets) == 0

    def test_random_data_roundtrip(self):
        """Random binary data of various sizes."""
        for size in [1, 10, 100, 500, 1064]:
            data = os.urandom(size)
            framed = HDLC.frame(data)
            packets = HDLC.deframe(framed)
            assert len(packets) == 1, f"Failed for size {size}"
            assert packets[0] == data, f"Data mismatch for size {size}"

    def test_garbage_before_frame(self):
        """Non-FLAG garbage bytes before a valid frame should be ignored."""
        data = b"valid packet"
        garbage = bytes([0x01, 0x02, 0x55, 0xAA, 0xFF] * 10)
        raw = garbage + HDLC.frame(data)
        packets = HDLC.deframe(raw)
        assert len(packets) == 1
        assert packets[0] == data

    def test_framed_data_starts_and_ends_with_flag(self):
        framed = HDLC.frame(b"test")
        assert framed[0] == HDLC.FLAG
        assert framed[-1] == HDLC.FLAG

    def test_escape_does_not_contain_raw_flag(self):
        """Escaped data must not contain raw FLAG bytes."""
        data = bytes([HDLC.FLAG] * 100)
        escaped = HDLC.escape(data)
        assert HDLC.FLAG not in escaped


class TestPadding:
    """Tests for fixed-size packet padding."""

    def test_roundtrip(self):
        data = b"hello"
        padded = Padding.pad(data, 1280)
        assert len(padded) == 1280
        assert Padding.unpad(padded) == data

    def test_roundtrip_random_sizes(self):
        for size in [1, 10, 100, 500, 638]:
            data = os.urandom(size)
            padded = Padding.pad(data, 1280)
            assert len(padded) == 1280
            assert Padding.unpad(padded) == data

    def test_output_always_same_size(self):
        """Differently-sized inputs produce same-sized padded output."""
        sizes = set()
        for data_len in [1, 50, 100, 300, 600]:
            padded = Padding.pad(os.urandom(data_len), 1280)
            sizes.add(len(padded))
        assert len(sizes) == 1  # All the same

    def test_padding_is_random(self):
        """Two pads of the same data should differ (random fill)."""
        data = b"same data"
        pad1 = Padding.pad(data, 1280)
        pad2 = Padding.pad(data, 1280)
        # The actual data portion is the same, but random fill differs
        assert pad1 != pad2
        # But both unpad to the same thing
        assert Padding.unpad(pad1) == Padding.unpad(pad2) == data

    def test_too_large_raises(self):
        max_data = Padding.max_payload(1280)
        # Exactly at limit should work
        Padding.pad(os.urandom(max_data), 1280)
        # One byte over should fail
        with pytest.raises(ValueError, match="too large"):
            Padding.pad(os.urandom(max_data + 1), 1280)

    def test_empty_data(self):
        padded = Padding.pad(b"", 1280)
        assert len(padded) == 1280
        assert Padding.unpad(padded) == b""

    def test_max_payload(self):
        assert Padding.max_payload(1280) == 1278

    def test_calculate_hw_mtu(self):
        # (1280 - 4) // 2 = 638
        assert Padding.calculate_hw_mtu(1280) == 638
        assert Padding.calculate_hw_mtu(512) == 254
        assert Padding.calculate_hw_mtu(2048) == 1022

    def test_encoded_output_size_is_fixed(self):
        """base85 of a fixed inner_size is always the same length."""
        size = Padding.encoded_output_size(1280)
        assert size > 0
        # Verify with actual encoding
        import base64
        for _ in range(10):
            data = os.urandom(500)
            framed = HDLC.frame(data)
            padded = Padding.pad(framed, 1280)
            encoded = base64.b85encode(padded)
            assert len(encoded) == size

    def test_unpad_corrupted_length(self):
        """Corrupted length header should raise, not crash."""
        # Length says 9999 but only 100 bytes available
        import struct
        bad = struct.pack("!H", 9999) + os.urandom(100)
        with pytest.raises(ValueError, match="available"):
            Padding.unpad(bad)

    def test_unpad_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            Padding.unpad(b"\x00")


class TestFullPipeline:
    """
    Test the complete encode/decode pipeline:
    raw -> HDLC frame -> pad -> base85 -> base85 decode -> unpad -> HDLC deframe -> raw
    """

    def test_roundtrip_small(self):
        import base64
        raw = os.urandom(50)
        framed = HDLC.frame(raw)
        padded = Padding.pad(framed, 1280)
        encoded = base64.b85encode(padded)

        d_padded = base64.b85decode(encoded)
        d_framed = Padding.unpad(d_padded)
        packets = HDLC.deframe(d_framed)

        assert len(packets) == 1
        assert packets[0] == raw

    def test_roundtrip_max_mtu(self):
        """Worst-case packet (all FLAG bytes) at calculated HW_MTU."""
        import base64
        inner_size = 1280
        hw_mtu = Padding.calculate_hw_mtu(inner_size)

        # Worst case: all FLAG bytes, every one gets escaped
        raw = bytes([HDLC.FLAG]) * hw_mtu
        framed = HDLC.frame(raw)
        padded = Padding.pad(framed, inner_size)
        encoded = base64.b85encode(padded)

        d_padded = base64.b85decode(encoded)
        d_framed = Padding.unpad(d_padded)
        packets = HDLC.deframe(d_framed)

        assert len(packets) == 1
        assert packets[0] == raw

    def test_all_encoded_outputs_same_size(self):
        """The whole point: every packet produces identical encoded size."""
        import base64
        inner_size = 1280
        expected = Padding.encoded_output_size(inner_size)

        for data_len in [1, 50, 200, 500, 638]:
            raw = os.urandom(data_len)
            framed = HDLC.frame(raw)
            padded = Padding.pad(framed, inner_size)
            encoded = base64.b85encode(padded)
            assert len(encoded) == expected, (
                f"data_len={data_len} produced {len(encoded)} bytes, expected {expected}"
            )
