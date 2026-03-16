"""Tests for encoding strategies."""

import os

import pytest

from rns_covert.encoding.strategies import Base64Encoder, BlobEncoder, get_encoder


class TestBlobEncoder:
    def test_roundtrip(self):
        data = os.urandom(1280)
        assert BlobEncoder.decode(BlobEncoder.encode(data)) == data

    def test_identity(self):
        """Blob encoder is passthrough -- no transformation."""
        data = os.urandom(500)
        assert BlobEncoder.encode(data) == data

    def test_empty(self):
        assert BlobEncoder.decode(BlobEncoder.encode(b"")) == b""


class TestBase64Encoder:
    def test_roundtrip(self):
        data = os.urandom(1280)
        encoded = Base64Encoder.encode(data)
        assert Base64Encoder.decode(encoded) == data

    def test_roundtrip_binary(self):
        data = bytes(range(256))
        assert Base64Encoder.decode(Base64Encoder.encode(data)) == data

    def test_output_is_ascii(self):
        data = os.urandom(500)
        encoded = Base64Encoder.encode(data)
        assert isinstance(encoded, str)
        encoded.encode("ascii")  # Should not raise

    def test_handles_whitespace(self):
        """Mail transport may add line breaks -- decoder must handle them."""
        data = os.urandom(200)
        encoded = Base64Encoder.encode(data)
        # Simulate mail transport adding line breaks
        mangled = "\n".join(encoded[i:i+76] for i in range(0, len(encoded), 76))
        assert Base64Encoder.decode(mangled) == data

    def test_empty(self):
        assert Base64Encoder.decode(Base64Encoder.encode(b"")) == b""


class TestEncoderRegistry:
    def test_get_blob(self):
        assert get_encoder("blob") is BlobEncoder

    def test_get_base64(self):
        assert get_encoder("base64") is Base64Encoder

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown encoding"):
            get_encoder("steganography_quantum_blockchain")
