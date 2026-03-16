"""Tests for MailInterface with mocked IMAP/SMTP connections."""

import email
import email.mime.multipart
import email.mime.text
import email.utils
import threading
from unittest.mock import MagicMock, patch

import pytest

from rns_covert.base import HDLC, Padding
from rns_covert.encoding.strategies import BlobEncoder


def _make_config(**overrides):
    """Create a minimal MailInterface config dict."""
    cfg = {
        "name": "TestMail",
        "account": "test@example.com",
        "password": "secret",
        "peer_address": "peer@example.com",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "imap_port": "993",
        "smtp_port": "465",
        "encoding": "blob",
        "locale": "en",
        "poll_interval": "9999",
        "batch_window": "9999",
        "max_sends_per_hour": "3600",
        "inner_size": "1280",
        "cleanup": "yes",
    }
    cfg.update(overrides)
    return cfg


def _build_test_email(from_addr, to_addr, payload_bytes, msg_id=None):
    """Build an RFC822 email with a blob attachment."""
    import email.encoders as _encoders
    import email.mime.base as _mime_base

    msg = email.mime.multipart.MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = "Test"
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = msg_id or email.utils.make_msgid()

    att = _mime_base.MIMEBase("application", "octet-stream")
    att.set_payload(payload_bytes)
    _encoders.encode_base64(att)
    att.add_header("Content-Disposition", "attachment", filename="data.bin")
    msg.attach(att)
    return msg


def _raw_bytes(msg):
    return msg.as_bytes()


def _make_mock_iface(mock_imap=None, mock_smtp=None, **overrides):
    """Create a MailInterface with bypassed __init__ and mocked connections."""
    from rns_covert.encoding.strategies import get_encoder
    from rns_covert.interfaces.mail import MailInterface
    from rns_covert.locale import get_locale
    from rns_covert.util import BoundedIdSet

    with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
        iface = MailInterface.__new__(MailInterface)

    defaults = {
        "account": "test@example.com",
        "password": "secret",
        "peer_address": "peer@example.com",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "imap_port": 993,
        "smtp_port": 465,
        "mailbox": "INBOX",
        "cleanup": False,
        "encoding_name": "blob",
        "inner_size": 1280,
        "name": "TestMail",
        "_conn_timeout": 30,
    }
    defaults.update(overrides)

    for k, v in defaults.items():
        setattr(iface, k, v)

    iface.encoder = get_encoder("blob")
    iface.locale = get_locale("en")
    iface._imap = mock_imap
    iface._smtp = mock_smtp
    iface._smtp_lock = threading.Lock()
    iface._shutdown_event = threading.Event()
    iface._sent_ids = BoundedIdSet()
    iface._processed_ids = BoundedIdSet()

    return iface


class TestPollPackets:
    """Test poll_packets with mocked IMAP."""

    @patch("rns_covert.interfaces.mail.imaplib")
    @patch("rns_covert.interfaces.mail.smtplib")
    def test_poll_extracts_packet(self, mock_smtplib, mock_imaplib):
        """poll_packets should extract a valid packet from an email."""
        mock_imap = MagicMock()
        iface = _make_mock_iface(mock_imap=mock_imap)

        raw_pkt = b"hello_reticulum"
        framed = HDLC.frame(raw_pkt)
        padded = Padding.pad(framed, 1280)
        blob = BlobEncoder.encode(padded)
        test_email = _build_test_email("peer@example.com", "test@example.com", blob, "<test123@example.com>")
        raw_email_bytes = _raw_bytes(test_email)

        mock_imap.noop.return_value = ("OK", [])
        mock_imap.select.return_value = ("OK", [b"1"])

        msg_header = b"Message-ID: <test123@example.com>\r\n"

        def uid_side_effect(cmd, *args):
            if cmd == 'search':
                return ("OK", [b"1"])
            if cmd == 'fetch':
                fetch_spec = args[1]
                if 'HEADER' in fetch_spec:
                    return ("OK", [(b"1 (BODY[HEADER.FIELDS (MESSAGE-ID)]", msg_header)])
                if 'RFC822' in fetch_spec:
                    return ("OK", [(b"1 (RFC822", raw_email_bytes)])
            return ("OK", [])

        mock_imap.uid.side_effect = uid_side_effect

        packets = iface.poll_packets()
        assert len(packets) == 1

        extracted = Padding.unpad(packets[0])
        result = HDLC.deframe(extracted)
        assert len(result) == 1
        assert result[0] == raw_pkt

    @patch("rns_covert.interfaces.mail.imaplib")
    @patch("rns_covert.interfaces.mail.smtplib")
    def test_poll_skips_own_messages(self, mock_smtplib, mock_imaplib):
        """Messages with IDs in _sent_ids should be skipped."""
        mock_imap = MagicMock()
        iface = _make_mock_iface(mock_imap=mock_imap)
        iface._sent_ids.add("<own_msg@example.com>")

        msg_header = b"Message-ID: <own_msg@example.com>\r\n"

        def uid_side_effect(cmd, *args):
            if cmd == 'search':
                return ("OK", [b"1"])
            if cmd == 'fetch':
                return ("OK", [(b"1 (BODY[HEADER.FIELDS (MESSAGE-ID)]", msg_header)])
            return ("OK", [])

        mock_imap.uid.side_effect = uid_side_effect
        mock_imap.select.return_value = ("OK", [b"1"])

        packets = iface.poll_packets()
        assert len(packets) == 0

    @patch("rns_covert.interfaces.mail.imaplib")
    @patch("rns_covert.interfaces.mail.smtplib")
    def test_poll_skips_already_processed(self, mock_smtplib, mock_imaplib):
        """Messages already in _processed_ids should be skipped."""
        mock_imap = MagicMock()
        iface = _make_mock_iface(mock_imap=mock_imap)
        iface._processed_ids.add("<old_msg@example.com>")

        msg_header = b"Message-ID: <old_msg@example.com>\r\n"

        def uid_side_effect(cmd, *args):
            if cmd == 'search':
                return ("OK", [b"1"])
            if cmd == 'fetch':
                return ("OK", [(b"1 (BODY[HEADER.FIELDS (MESSAGE-ID)]", msg_header)])
            return ("OK", [])

        mock_imap.uid.side_effect = uid_side_effect
        mock_imap.select.return_value = ("OK", [b"1"])

        packets = iface.poll_packets()
        assert len(packets) == 0

    def test_poll_imap_abort_no_deadlock(self):
        """IMAP4.abort during poll should not deadlock."""
        import imaplib as _imaplib

        mock_imap = MagicMock()
        iface = _make_mock_iface(mock_imap=mock_imap)

        mock_imap.noop.return_value = ("OK", [])
        mock_imap.select.side_effect = _imaplib.IMAP4.abort("connection reset")

        packets = iface.poll_packets()
        assert packets == []
        assert iface._imap is None


class TestEnsureImap:
    """Test _ensure_imap retry logic."""

    @patch("rns_covert.interfaces.mail.imaplib")
    def test_reconnects_on_noop_failure(self, mock_imaplib):
        mock_imap_bad = MagicMock()
        mock_imap_bad.noop.side_effect = Exception("connection lost")

        mock_imap_good = MagicMock()
        mock_imap_good.login.return_value = ("OK", [])
        mock_imap_good.select.return_value = ("OK", [b"1"])

        mock_imaplib.IMAP4_SSL.return_value = mock_imap_good

        iface = _make_mock_iface(mock_imap=mock_imap_bad)

        iface._ensure_imap()
        assert iface._imap is mock_imap_good
        # Old connection should have been closed
        mock_imap_bad.logout.assert_called_once()

    @patch("rns_covert.interfaces.mail.imaplib")
    def test_raises_after_max_retries(self, mock_imaplib):
        mock_imap = MagicMock()
        mock_imap.noop.side_effect = Exception("fail")
        mock_imaplib.IMAP4_SSL.return_value = mock_imap
        mock_imap.login.side_effect = Exception("login failed")

        iface = _make_mock_iface(mock_imap=mock_imap)

        with pytest.raises(ConnectionError, match="failed after 3 attempts"):
            iface._ensure_imap()

    @patch("rns_covert.interfaces.mail.imaplib")
    def test_closes_leaked_connection_on_login_failure(self, mock_imaplib):
        """If login fails after IMAP4_SSL, the socket should be closed."""
        mock_conn = MagicMock()
        mock_conn.login.side_effect = Exception("auth failed")
        mock_imaplib.IMAP4_SSL.return_value = mock_conn

        iface = _make_mock_iface()

        with pytest.raises(ConnectionError):
            iface._ensure_imap()

        # Each failed attempt should close the connection
        assert mock_conn.logout.call_count == 3

    def test_respects_shutdown_event(self):
        """_ensure_imap should raise immediately if shutting down."""
        iface = _make_mock_iface()
        iface._shutdown_event.set()

        with pytest.raises(ConnectionError, match="shutting down"):
            iface._ensure_imap()


class TestEnsureSmtp:
    def test_respects_shutdown_event(self):
        """_ensure_smtp should raise immediately if shutting down."""
        iface = _make_mock_iface()
        iface._shutdown_event.set()

        with pytest.raises(ConnectionError, match="shutting down"):
            iface._ensure_smtp()


class TestSendPacket:
    """Test send_packet with mocked SMTP."""

    @patch("rns_covert.interfaces.mail.smtplib")
    def test_send_packet_uses_persistent_smtp(self, mock_smtplib):
        mock_smtp = MagicMock()
        mock_smtp.noop.return_value = (250, b"OK")

        iface = _make_mock_iface(mock_smtp=mock_smtp)

        raw_pkt = b"test_data"
        framed = HDLC.frame(raw_pkt)
        padded = Padding.pad(framed, 1280)

        iface.send_packet(padded)

        mock_smtp.sendmail.assert_called_once()
        mock_smtplib.SMTP_SSL.assert_not_called()


class TestBatchCleanup:
    """Test _batch_cleanup uses UID commands with single expunge."""

    def test_batch_cleanup_single_expunge(self):
        mock_imap = MagicMock()
        mock_imap.uid.return_value = ("OK", [])

        iface = _make_mock_iface(mock_imap=mock_imap)
        iface.PROCESSED_FOLDER = "Processed"

        iface._batch_cleanup([b"1", b"2", b"3"])

        uid_calls = [c[0] for c in mock_imap.uid.call_args_list]
        # Should have copy + store for each UID
        assert ('copy', b"1", "Processed") in uid_calls
        assert ('copy', b"2", "Processed") in uid_calls
        assert ('copy', b"3", "Processed") in uid_calls
        assert ('store', b"1", '+FLAGS', '\\Deleted') in uid_calls
        assert ('store', b"2", '+FLAGS', '\\Deleted') in uid_calls
        assert ('store', b"3", '+FLAGS', '\\Deleted') in uid_calls
        # Only ONE expunge at the end
        mock_imap.expunge.assert_called_once()

    def test_cleanup_message_delegates_to_batch(self):
        """_cleanup_message should delegate to _batch_cleanup."""
        mock_imap = MagicMock()
        mock_imap.uid.return_value = ("OK", [])

        iface = _make_mock_iface(mock_imap=mock_imap)
        iface.PROCESSED_FOLDER = "Processed"

        iface._cleanup_message(b"123")

        calls = [c[0] for c in mock_imap.uid.call_args_list]
        assert ('copy', b"123", "Processed") in calls
        assert ('store', b"123", '+FLAGS', '\\Deleted') in calls
        mock_imap.expunge.assert_called_once()


class TestCreateSmtp:
    """Test _create_smtp for STARTTLS support and socket leak prevention."""

    @patch("rns_covert.interfaces.mail.smtplib")
    def test_port_465_uses_smtp_ssl(self, mock_smtplib):
        mock_smtp = MagicMock()
        mock_smtplib.SMTP_SSL.return_value = mock_smtp

        iface = _make_mock_iface()

        result = iface._create_smtp()
        mock_smtplib.SMTP_SSL.assert_called_once_with("smtp.example.com", 465, timeout=30)
        mock_smtp.login.assert_called_once()
        assert result is mock_smtp

    @patch("rns_covert.interfaces.mail.smtplib")
    def test_port_587_uses_starttls(self, mock_smtplib):
        mock_smtp = MagicMock()
        mock_smtplib.SMTP.return_value = mock_smtp

        iface = _make_mock_iface(smtp_port=587)

        result = iface._create_smtp()
        mock_smtplib.SMTP.assert_called_once_with("smtp.example.com", 587, timeout=30)
        mock_smtp.ehlo.assert_called()
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once()
        assert result is mock_smtp

    @patch("rns_covert.interfaces.mail.smtplib")
    def test_port_465_closes_on_login_failure(self, mock_smtplib):
        mock_smtp = MagicMock()
        mock_smtp.login.side_effect = Exception("auth failed")
        mock_smtplib.SMTP_SSL.return_value = mock_smtp

        iface = _make_mock_iface()

        with pytest.raises(Exception, match="auth failed"):
            iface._create_smtp()

        mock_smtp.close.assert_called_once()

    @patch("rns_covert.interfaces.mail.smtplib")
    def test_port_587_closes_on_starttls_failure(self, mock_smtplib):
        mock_smtp = MagicMock()
        mock_smtp.starttls.side_effect = Exception("TLS failed")
        mock_smtplib.SMTP.return_value = mock_smtp

        iface = _make_mock_iface(smtp_port=587)

        with pytest.raises(Exception, match="TLS failed"):
            iface._create_smtp()

        mock_smtp.close.assert_called_once()
