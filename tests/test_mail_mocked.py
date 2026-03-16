"""Tests for MailInterface with mocked IMAP/SMTP connections."""

import email
import email.mime.multipart
import email.mime.text
import email.utils
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


class TestPollPackets:
    """Test poll_packets with mocked IMAP."""

    @patch("rns_covert.interfaces.mail.imaplib")
    @patch("rns_covert.interfaces.mail.smtplib")
    def test_poll_extracts_packet(self, mock_smtplib, mock_imaplib):
        """poll_packets should extract a valid packet from an email."""
        from rns_covert.interfaces.mail import MailInterface

        mock_imap = MagicMock()
        mock_imaplib.IMAP4_SSL.return_value = mock_imap
        mock_imap.login.return_value = ("OK", [])
        mock_imap.select.return_value = ("OK", [b"1"])
        mock_imap.create.return_value = ("OK", [])

        # start_transport: _scan_existing_messages returns no messages
        mock_imap.uid.return_value = ("OK", [b""])

        mock_smtp = MagicMock()
        mock_smtplib.SMTP_SSL.return_value = mock_smtp

        iface = MailInterface.__new__(MailInterface)
        # Manually init just the parts we need, bypassing full __init__
        cfg = _make_config()

        with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
            iface = MailInterface.__new__(MailInterface)

        # Set required attributes manually
        import threading

        from rns_covert.encoding.strategies import get_encoder
        from rns_covert.locale import get_locale
        from rns_covert.util import BoundedIdSet

        iface.account = cfg["account"]
        iface.password = cfg["password"]
        iface.peer_address = cfg["peer_address"]
        iface.imap_host = cfg["imap_host"]
        iface.smtp_host = cfg["smtp_host"]
        iface.imap_port = 993
        iface.smtp_port = 465
        iface.mailbox = "INBOX"
        iface.cleanup = False
        iface.encoding_name = "blob"
        iface.encoder = get_encoder("blob")
        iface.locale = get_locale("en")
        iface._imap = mock_imap
        iface._smtp = None
        iface._imap_lock = threading.Lock()
        iface._imap_lock = threading.Lock()
        iface._smtp_lock = threading.Lock()
        iface._sent_ids = BoundedIdSet()
        iface._processed_ids = BoundedIdSet()
        iface.inner_size = 1280
        iface.name = "TestMail"

        # Build a test email
        raw_pkt = b"hello_reticulum"
        framed = HDLC.frame(raw_pkt)
        padded = Padding.pad(framed, 1280)
        blob = BlobEncoder.encode(padded)
        test_email = _build_test_email("peer@example.com", "test@example.com", blob, "<test123@example.com>")
        raw_email_bytes = _raw_bytes(test_email)

        # Setup mock responses
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

        # Decode the extracted payload
        extracted = Padding.unpad(packets[0])
        result = HDLC.deframe(extracted)
        assert len(result) == 1
        assert result[0] == raw_pkt

    @patch("rns_covert.interfaces.mail.imaplib")
    @patch("rns_covert.interfaces.mail.smtplib")
    def test_poll_skips_own_messages(self, mock_smtplib, mock_imaplib):
        """Messages with IDs in _sent_ids should be skipped."""
        import threading

        from rns_covert.encoding.strategies import get_encoder
        from rns_covert.interfaces.mail import MailInterface
        from rns_covert.locale import get_locale
        from rns_covert.util import BoundedIdSet

        mock_imap = MagicMock()

        with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
            iface = MailInterface.__new__(MailInterface)

        iface.account = "test@example.com"
        iface.password = "secret"
        iface.peer_address = "peer@example.com"
        iface.imap_host = "imap.example.com"
        iface.smtp_host = "smtp.example.com"
        iface.imap_port = 993
        iface.smtp_port = 465
        iface.mailbox = "INBOX"
        iface.cleanup = False
        iface.encoding_name = "blob"
        iface.encoder = get_encoder("blob")
        iface.locale = get_locale("en")
        iface._imap = mock_imap
        iface._smtp = None
        iface._imap_lock = threading.Lock()
        iface._smtp_lock = threading.Lock()
        iface._sent_ids = BoundedIdSet()
        iface._processed_ids = BoundedIdSet()
        iface.inner_size = 1280
        iface.name = "TestMail"

        # Mark message as sent by us
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
        import threading

        from rns_covert.encoding.strategies import get_encoder
        from rns_covert.interfaces.mail import MailInterface
        from rns_covert.locale import get_locale
        from rns_covert.util import BoundedIdSet

        mock_imap = MagicMock()

        with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
            iface = MailInterface.__new__(MailInterface)

        iface.account = "test@example.com"
        iface.password = "secret"
        iface.peer_address = "peer@example.com"
        iface.imap_host = "imap.example.com"
        iface.smtp_host = "smtp.example.com"
        iface.imap_port = 993
        iface.smtp_port = 465
        iface.mailbox = "INBOX"
        iface.cleanup = False
        iface.encoding_name = "blob"
        iface.encoder = get_encoder("blob")
        iface.locale = get_locale("en")
        iface._imap = mock_imap
        iface._smtp = None
        iface._imap_lock = threading.Lock()
        iface._smtp_lock = threading.Lock()
        iface._sent_ids = BoundedIdSet()
        iface._processed_ids = BoundedIdSet()
        iface.inner_size = 1280
        iface.name = "TestMail"

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


class TestEnsureImap:
    """Test _ensure_imap retry logic."""

    @patch("rns_covert.interfaces.mail.imaplib")
    def test_reconnects_on_noop_failure(self, mock_imaplib):
        from rns_covert.interfaces.mail import MailInterface

        mock_imap_bad = MagicMock()
        mock_imap_bad.noop.side_effect = Exception("connection lost")

        mock_imap_good = MagicMock()
        mock_imap_good.login.return_value = ("OK", [])
        mock_imap_good.select.return_value = ("OK", [b"1"])

        mock_imaplib.IMAP4_SSL.return_value = mock_imap_good

        with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
            iface = MailInterface.__new__(MailInterface)

        iface.imap_host = "imap.example.com"
        iface.imap_port = 993
        iface.account = "test@example.com"
        iface.password = "secret"
        iface.mailbox = "INBOX"
        iface._conn_timeout = 30
        iface._imap = mock_imap_bad

        iface._ensure_imap()

        # Should have created a new connection
        assert iface._imap is mock_imap_good

    @patch("rns_covert.interfaces.mail.imaplib")
    def test_raises_after_max_retries(self, mock_imaplib):
        from rns_covert.interfaces.mail import MailInterface

        mock_imap = MagicMock()
        mock_imap.noop.side_effect = Exception("fail")
        mock_imaplib.IMAP4_SSL.return_value = mock_imap
        mock_imap.login.side_effect = Exception("login failed")

        with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
            iface = MailInterface.__new__(MailInterface)

        iface.imap_host = "imap.example.com"
        iface.imap_port = 993
        iface.account = "test@example.com"
        iface.password = "secret"
        iface.mailbox = "INBOX"
        iface._imap = mock_imap

        with pytest.raises(ConnectionError, match="failed after 3 attempts"):
            iface._ensure_imap()


class TestSendPacket:
    """Test send_packet with mocked SMTP."""

    @patch("rns_covert.interfaces.mail.smtplib")
    def test_send_packet_uses_persistent_smtp(self, mock_smtplib):
        import threading

        from rns_covert.encoding.strategies import get_encoder
        from rns_covert.interfaces.mail import MailInterface
        from rns_covert.locale import get_locale
        from rns_covert.util import BoundedIdSet

        mock_smtp = MagicMock()
        mock_smtp.noop.return_value = (250, b"OK")

        with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
            iface = MailInterface.__new__(MailInterface)

        iface.account = "test@example.com"
        iface.password = "secret"
        iface.peer_address = "peer@example.com"
        iface.smtp_host = "smtp.example.com"
        iface.smtp_port = 465
        iface.encoding_name = "blob"
        iface.encoder = get_encoder("blob")
        iface.locale = get_locale("en")
        iface._smtp = mock_smtp
        iface._imap_lock = threading.Lock()
        iface._smtp_lock = threading.Lock()
        iface._sent_ids = BoundedIdSet()
        iface.inner_size = 1280

        raw_pkt = b"test_data"
        framed = HDLC.frame(raw_pkt)
        padded = Padding.pad(framed, 1280)

        iface.send_packet(padded)

        mock_smtp.sendmail.assert_called_once()
        # SMTP connection should be reused, not recreated
        mock_smtplib.SMTP_SSL.assert_not_called()


class TestCleanupMessage:
    """Test _cleanup_message uses UID commands."""

    def test_cleanup_uses_uid(self):
        from rns_covert.interfaces.mail import MailInterface

        mock_imap = MagicMock()
        mock_imap.uid.return_value = ("OK", [])

        with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
            iface = MailInterface.__new__(MailInterface)

        iface._imap = mock_imap
        iface.PROCESSED_FOLDER = "Processed"

        iface._cleanup_message(b"123")

        calls = [c[0] for c in mock_imap.uid.call_args_list]
        assert ('copy', b"123", "Processed") in calls
        assert ('store', b"123", '+FLAGS', '\\Deleted') in calls
        mock_imap.expunge.assert_called_once()


class TestCreateSmtp:
    """Test _create_smtp for STARTTLS support."""

    @patch("rns_covert.interfaces.mail.smtplib")
    def test_port_465_uses_smtp_ssl(self, mock_smtplib):
        from rns_covert.interfaces.mail import MailInterface

        mock_smtp = MagicMock()
        mock_smtplib.SMTP_SSL.return_value = mock_smtp

        with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
            iface = MailInterface.__new__(MailInterface)

        iface.smtp_host = "smtp.example.com"
        iface.smtp_port = 465
        iface.account = "test@example.com"
        iface.password = "secret"
        iface._conn_timeout = 30

        result = iface._create_smtp()
        mock_smtplib.SMTP_SSL.assert_called_once_with("smtp.example.com", 465, timeout=30)
        mock_smtp.login.assert_called_once()
        assert result is mock_smtp

    @patch("rns_covert.interfaces.mail.smtplib")
    def test_port_587_uses_starttls(self, mock_smtplib):
        from rns_covert.interfaces.mail import MailInterface

        mock_smtp = MagicMock()
        mock_smtplib.SMTP.return_value = mock_smtp

        with patch.object(MailInterface, "__init__", lambda self, *a, **kw: None):
            iface = MailInterface.__new__(MailInterface)

        iface.smtp_host = "smtp.example.com"
        iface.smtp_port = 587
        iface.account = "test@example.com"
        iface.password = "secret"
        iface._conn_timeout = 30

        result = iface._create_smtp()
        mock_smtplib.SMTP.assert_called_once_with("smtp.example.com", 587, timeout=30)
        mock_smtp.ehlo.assert_called()
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once()
        assert result is mock_smtp
