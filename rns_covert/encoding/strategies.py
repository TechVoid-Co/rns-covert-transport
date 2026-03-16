"""
Encoding strategies for covert packet transport.

Two modes only:
  - blob:   Raw binary attachment. Reticulum already encrypts everything,
            so the attachment is an opaque binary blob indistinguishable
            from any compressed/encrypted file. Best option.
  - base64: Packet as base64 text in the email body. Fallback for
            services that strip binary attachments.
"""

import base64
import struct
import os


class BlobEncoder:
    """
    Raw binary blob encoding.

    The packet (already padded to fixed size) goes straight into
    the attachment as binary. Reticulum's encryption means the
    bytes are indistinguishable from random data -- same as any
    .zip, .enc, .bak, .dat file someone might email.

    No extra wrapping, no checksums, no markers. The padding
    layer already guarantees fixed size and the HDLC framing
    provides packet boundaries. Adding anything else is just
    fingerprinting ourselves.
    """

    @staticmethod
    def encode(data: bytes) -> bytes:
        return data

    @staticmethod
    def decode(data: bytes) -> bytes:
        return data


class Base64Encoder:
    """
    Base64 text encoding.

    For services that strip binary attachments or where only
    text transport is available. The packet becomes a base64
    string that can go in an email body, chat message, note, etc.
    """

    @staticmethod
    def encode(data: bytes) -> str:
        return base64.b64encode(data).decode("ascii")

    @staticmethod
    def decode(text: str) -> bytes:
        # Strip any whitespace that mail transport may have added
        cleaned = text.strip().replace("\n", "").replace("\r", "").replace(" ", "")
        return base64.b64decode(cleaned)


_ENCODERS = {
    "blob": BlobEncoder,
    "base64": Base64Encoder,
}


def get_encoder(name: str):
    enc = _ENCODERS.get(name)
    if enc is None:
        raise ValueError(
            f"Unknown encoding '{name}'. Available: {', '.join(_ENCODERS.keys())}"
        )
    return enc
