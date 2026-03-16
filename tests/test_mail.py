"""Tests for MailInterface email construction and extraction."""

import email
import email.encoders
import email.mime.base
import email.mime.multipart
import email.mime.text
import os

import pytest

from rns_covert.base import HDLC, Padding
from rns_covert.encoding.strategies import Base64Encoder, BlobEncoder
from rns_covert.locale import EnglishLocale, RussianLocale

INNER_SIZE = 1280


class TestBlobEmailRoundtrip:
    """Full pipeline through MIME email with blob attachment."""

    def _roundtrip(self, locale_cls):
        original = os.urandom(500)

        framed = HDLC.frame(original)
        padded = Padding.pad(framed, INNER_SIZE)
        blob = BlobEncoder.encode(padded)

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = "a@example.com"
        msg["To"] = "b@example.com"
        msg["Subject"] = locale_cls.generate_subject()
        body = locale_cls.generate_body(has_attachment=True)
        if body:
            msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))

        att = email.mime.base.MIMEBase("application", "octet-stream")
        att.set_payload(blob)
        email.encoders.encode_base64(att)
        att.add_header("Content-Disposition", "attachment", filename=locale_cls.generate_filename())
        msg.attach(att)

        parsed = email.message_from_bytes(msg.as_bytes())

        for part in parsed.walk():
            if part.get_content_disposition() == "attachment":
                payload = part.get_payload(decode=True)
                decoded_padded = BlobEncoder.decode(payload)
                inner = Padding.unpad(decoded_padded)
                packets = HDLC.deframe(inner)

                assert len(packets) == 1
                assert packets[0] == original
                return

        pytest.fail("No attachment found")

    def test_roundtrip_ru(self):
        self._roundtrip(RussianLocale)

    def test_roundtrip_en(self):
        self._roundtrip(EnglishLocale)

    def test_max_mtu(self):
        hw_mtu = Padding.calculate_hw_mtu(INNER_SIZE)
        original = bytes([HDLC.FLAG]) * hw_mtu

        framed = HDLC.frame(original)
        padded = Padding.pad(framed, INNER_SIZE)
        blob = BlobEncoder.encode(padded)

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = "a@example.com"
        msg["To"] = "b@example.com"
        msg["Subject"] = "Files"
        msg.attach(email.mime.text.MIMEText("See attached.", "plain", "utf-8"))

        att = email.mime.base.MIMEBase("application", "octet-stream")
        att.set_payload(blob)
        email.encoders.encode_base64(att)
        att.add_header("Content-Disposition", "attachment", filename="data.bin")
        msg.attach(att)

        parsed = email.message_from_bytes(msg.as_bytes())
        for part in parsed.walk():
            if part.get_content_disposition() == "attachment":
                payload = part.get_payload(decode=True)
                inner = Padding.unpad(BlobEncoder.decode(payload))
                packets = HDLC.deframe(inner)
                assert len(packets) == 1
                assert packets[0] == original
                return

        pytest.fail("No attachment found")


class TestBase64EmailRoundtrip:
    def test_roundtrip(self):
        original = os.urandom(500)

        framed = HDLC.frame(original)
        padded = Padding.pad(framed, INNER_SIZE)
        b64_text = Base64Encoder.encode(padded)

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = "a@example.com"
        msg["To"] = "b@example.com"
        msg["Subject"] = "Update"
        msg.attach(email.mime.text.MIMEText(b64_text, "plain", "utf-8"))

        parsed = email.message_from_bytes(msg.as_bytes())
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                text = part.get_payload(decode=True).decode("utf-8")
                decoded_padded = Base64Encoder.decode(text)
                inner = Padding.unpad(decoded_padded)
                packets = HDLC.deframe(inner)

                assert len(packets) == 1
                assert packets[0] == original
                return

        pytest.fail("No text body found")


class TestFixedSize:
    def test_all_blobs_same_size(self):
        sizes = set()
        for data_len in [1, 50, 200, 500, 638]:
            framed = HDLC.frame(os.urandom(data_len))
            padded = Padding.pad(framed, INNER_SIZE)
            blob = BlobEncoder.encode(padded)
            sizes.add(len(blob))
        assert len(sizes) == 1

    def test_all_base64_same_size(self):
        sizes = set()
        for data_len in [1, 50, 200, 500, 638]:
            framed = HDLC.frame(os.urandom(data_len))
            padded = Padding.pad(framed, INNER_SIZE)
            b64 = Base64Encoder.encode(padded)
            sizes.add(len(b64))
        assert len(sizes) == 1


class TestEdgeCases:
    def test_all_zeros(self):
        original = b"\x00" * 500
        framed = HDLC.frame(original)
        padded = Padding.pad(framed, INNER_SIZE)
        inner = Padding.unpad(BlobEncoder.decode(BlobEncoder.encode(padded)))
        assert HDLC.deframe(inner)[0] == original

    def test_all_flag_bytes(self):
        original = bytes([HDLC.FLAG]) * 100
        framed = HDLC.frame(original)
        padded = Padding.pad(framed, INNER_SIZE)
        inner = Padding.unpad(BlobEncoder.decode(BlobEncoder.encode(padded)))
        assert HDLC.deframe(inner)[0] == original

    def test_single_byte(self):
        original = b"\x42"
        framed = HDLC.frame(original)
        padded = Padding.pad(framed, INNER_SIZE)
        inner = Padding.unpad(BlobEncoder.decode(BlobEncoder.encode(padded)))
        assert HDLC.deframe(inner)[0] == original
