"""
MailInterface -- Reticulum transport over standard IMAP/SMTP email.

Works with any email provider: Yandex, Gmail, Mail.ru, Outlook, or
any server that supports IMAP and SMTP over SSL/TLS.

Reticulum packets are encrypted before they reach this layer. The
attachment is an opaque binary blob indistinguishable from any
compressed or encrypted file. Emails are crafted with locale-
appropriate subjects, filenames, and body text to blend in with
normal correspondence.

Configuration in ~/.reticulum/config:

    [[Mail Transport]]
      type = MailInterface
      enabled = yes
      account = user@example.com
      password = app_password
      peer_address = peer@example.com
      imap_host = imap.example.com
      imap_port = 993
      smtp_host = smtp.example.com
      smtp_port = 465
      locale = en
      encoding = blob
      poll_interval = 30
      inner_size = 1280
      max_sends_per_hour = 30
      batch_window = 5

Place MailInterface.py (the drop-in file) in ~/.reticulum/interfaces/
"""

import email
import email.encoders
import email.header
import email.mime.base
import email.mime.multipart
import email.mime.text
import email.utils
import imaplib
import smtplib
import threading
from typing import Optional

import RNS
from RNS.Interfaces.Interface import Interface

from rns_covert.base import HDLC, CovertInterface, Padding
from rns_covert.encoding.strategies import get_encoder
from rns_covert.locale import DEFAULT_LOCALE, get_locale
from rns_covert.util import BoundedIdSet


class MailInterface(CovertInterface):
    """
    Reticulum custom interface -- transport over standard IMAP/SMTP.

    Supports any email provider. No provider-specific logic.

    encoding = blob    -- binary attachment (default, recommended)
    encoding = base64  -- base64 text in email body (fallback)

    locale = ru        -- Russian email camouflage (default)
    locale = en        -- English email camouflage
    locale = neutral   -- Language-neutral, ASCII-only
    """

    # No hardcoded provider defaults -- all must be specified in config,
    # except for these sensible fallbacks.
    DEFAULT_IMAP_PORT = 993
    DEFAULT_SMTP_PORT = 465
    DEFAULT_MAILBOX = "INBOX"
    PROCESSED_FOLDER = "Processed"
    DEFAULT_CONN_TIMEOUT = 30

    def __init__(self, owner, configuration):
        c = Interface.get_config_obj(configuration)

        # Required
        self.account      = c["account"]
        self.password     = c["password"]
        self.peer_address = c["peer_address"]
        self.imap_host    = c["imap_host"]
        self.smtp_host    = c["smtp_host"]

        # Optional with defaults
        self.imap_port    = int(c.get("imap_port", self.DEFAULT_IMAP_PORT))
        self.smtp_port    = int(c.get("smtp_port", self.DEFAULT_SMTP_PORT))
        self.mailbox      = c.get("mailbox", self.DEFAULT_MAILBOX)
        self.cleanup      = c.get("cleanup", "yes").lower() in ("yes", "true", "1")
        self._conn_timeout = int(c.get("conn_timeout", self.DEFAULT_CONN_TIMEOUT))

        # Encoding and locale
        self.encoding_name = c.get("encoding", "blob")
        self.encoder       = get_encoder(self.encoding_name)
        locale_name        = c.get("locale", DEFAULT_LOCALE)
        self.locale        = get_locale(locale_name)

        # Connection state
        self._imap = None
        self._imap_lock = threading.Lock()
        self._smtp = None
        self._smtp_lock = threading.Lock()

        # Message-ID tracking (for single-account / self-to-self operation)
        self._sent_ids = BoundedIdSet(maxlen=10000)
        self._processed_ids = BoundedIdSet(maxlen=10000)

        super().__init__(owner, configuration)

    # ------------------------------------------------------------------
    #  Transport lifecycle
    # ------------------------------------------------------------------

    def start_transport(self):
        RNS.log(f"Connecting IMAP to {self.imap_host}:{self.imap_port}...", RNS.LOG_VERBOSE)
        with self._imap_lock:
            self._imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=self._conn_timeout)
            self._imap.login(self.account, self.password)
            self._imap.select(self.mailbox)

            if self.cleanup:
                try:
                    self._imap.create(self.PROCESSED_FOLDER)
                except Exception as e:
                    RNS.log(f"Could not create '{self.PROCESSED_FOLDER}' (may exist): {e}", RNS.LOG_DEBUG)

        RNS.log(f"Verifying SMTP to {self.smtp_host}:{self.smtp_port}...", RNS.LOG_VERBOSE)
        smtp = self._create_smtp()
        smtp.quit()

        RNS.log(f"Mail transport ready: {self.account} <-> {self.peer_address}", RNS.LOG_VERBOSE)

        # Mark all existing messages as processed
        self._scan_existing_messages()

    def _create_smtp(self):
        if self.smtp_port == 587:
            smtp = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self._conn_timeout)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()  # RFC 3207 §4.2: re-negotiate capabilities after STARTTLS
        else:
            smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self._conn_timeout)
        smtp.login(self.account, self.password)
        return smtp

    def _scan_existing_messages(self):
        try:
            with self._imap_lock:
                status, data = self._imap.uid('search', None, 'ALL')
                if status == "OK" and data[0]:
                    uids = data[0].split()
                    for uid in uids:
                        try:
                            status, hdr_data = self._imap.uid(
                                'fetch', uid, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])'
                            )
                            if status == "OK" and hdr_data[0] and len(hdr_data[0]) > 1:
                                raw_header = hdr_data[0][1]
                                msg_id = email.message_from_bytes(raw_header).get("Message-ID", "").strip()
                                dedup_key = msg_id if msg_id else uid.decode()
                                self._processed_ids.add(dedup_key)
                        except Exception as e:
                            RNS.log(f"Error scanning existing message {uid}: {e}", RNS.LOG_DEBUG)
                    RNS.log(f"Marked {len(uids)} existing messages as processed", RNS.LOG_VERBOSE)
        except Exception as e:
            RNS.log(f"Error scanning existing messages: {e}", RNS.LOG_DEBUG)

    def send_packet(self, encoded_data: bytes):
        msg = self._build_email(encoded_data)

        msg_id = msg["Message-ID"]
        if msg_id:
            self._sent_ids.add(msg_id.strip())

        with self._smtp_lock:
            self._ensure_smtp()
            try:
                self._smtp.sendmail(self.account, [self.peer_address], msg.as_bytes())
            except Exception:
                self._smtp = None
                raise

    def poll_packets(self) -> list:
        packets = []
        try:
            with self._imap_lock:
                self._ensure_imap()
                self._imap.select(self.mailbox)

                _escaped_peer = self.peer_address.replace('\\', '\\\\').replace('"', '\\"')
                status, data = self._imap.uid('search', None, f'(FROM "{_escaped_peer}")')
                if status != "OK" or not data[0]:
                    return []

                for uid in data[0].split():
                    try:
                        status, hdr_data = self._imap.uid(
                            'fetch', uid, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])'
                        )
                        if status != "OK":
                            continue

                        raw_header = hdr_data[0][1] if hdr_data[0] and len(hdr_data[0]) > 1 else b""
                        msg_id = email.message_from_bytes(raw_header).get("Message-ID", "").strip()
                        dedup_key = msg_id if msg_id else uid.decode()

                        if dedup_key in self._sent_ids:
                            continue

                        if dedup_key in self._processed_ids:
                            continue

                        payload = self._extract_packet(uid)
                        if payload is not None:
                            packets.append(payload)

                        self._processed_ids.add(dedup_key)

                        if self.cleanup:
                            self._cleanup_message(uid)

                    except Exception as e:
                        RNS.log(f"Error processing message {uid}: {e}", RNS.LOG_DEBUG)

        except imaplib.IMAP4.abort:
            RNS.log(f"IMAP connection reset on {self}, will reconnect", RNS.LOG_DEBUG)
            with self._imap_lock:
                self._imap = None

        return packets

    def stop_transport(self):
        try:
            with self._imap_lock:
                if self._imap:
                    self._imap.close()
                    self._imap.logout()
        except Exception as e:
            RNS.log(f"Error closing IMAP on {self}: {e}", RNS.LOG_DEBUG)
        self._imap = None

        try:
            if self._smtp:
                self._smtp.quit()
        except Exception as e:
            RNS.log(f"Error closing SMTP on {self}: {e}", RNS.LOG_DEBUG)
        self._smtp = None

    # ------------------------------------------------------------------
    #  Email construction
    # ------------------------------------------------------------------

    def _build_email(self, encoded_data: bytes):
        if self.encoding_name == "base64":
            return self._build_base64_email(encoded_data)
        else:
            return self._build_blob_email(encoded_data)

    def _build_blob_email(self, encoded_data: bytes):
        domain = self.account.partition("@")[2] or self.account

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = self.account
        msg["To"] = self.peer_address
        msg["Subject"] = email.header.Header(self.locale.generate_subject(), "utf-8")
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=domain)

        body_text = self.locale.generate_body(has_attachment=True)
        if body_text:
            msg.attach(email.mime.text.MIMEText(body_text, "plain", "utf-8"))

        filename = self.locale.generate_filename()
        blob = self.encoder.encode(encoded_data)
        attachment = email.mime.base.MIMEBase("application", "octet-stream")
        attachment.set_payload(blob)
        email.encoders.encode_base64(attachment)
        attachment.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(attachment)

        return msg

    def _build_base64_email(self, encoded_data: bytes):
        domain = self.account.partition("@")[2] or self.account

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = self.account
        msg["To"] = self.peer_address
        msg["Subject"] = email.header.Header(self.locale.generate_subject(), "utf-8")
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=domain)

        b64_text = self.encoder.encode(encoded_data)
        msg.attach(email.mime.text.MIMEText(b64_text, "plain", "utf-8"))

        return msg

    # ------------------------------------------------------------------
    #  Packet extraction
    # ------------------------------------------------------------------

    def _extract_packet(self, uid: bytes) -> Optional[bytes]:
        status, data = self._imap.uid('fetch', uid, '(RFC822)')
        if status != "OK":
            return None

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        if self.encoding_name == "base64":
            return self._extract_base64(msg)
        else:
            return self._extract_blob(msg)

    def _extract_blob(self, msg) -> Optional[bytes]:
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                payload = part.get_payload(decode=True)
                if payload and len(payload) > 0:
                    try:
                        return self.encoder.decode(payload)
                    except Exception as e:
                        RNS.log(f"Could not decode attachment on {self}: {e}", RNS.LOG_DEBUG)
                        continue
        return None

    def _extract_base64(self, msg) -> Optional[bytes]:
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                text = part.get_payload(decode=True)
                if text:
                    try:
                        return self.encoder.decode(text.decode("utf-8", errors="ignore"))
                    except Exception as e:
                        RNS.log(f"Could not decode base64 part on {self}: {e}", RNS.LOG_DEBUG)
                        continue
        return None

    # ------------------------------------------------------------------
    #  IMAP helpers
    # ------------------------------------------------------------------

    def _ensure_imap(self, max_retries=3):
        for _attempt in range(max_retries):
            if self._imap is None:
                try:
                    self._imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=self._conn_timeout)
                    self._imap.login(self.account, self.password)
                    self._imap.select(self.mailbox)
                    return
                except Exception as e:
                    RNS.log(f"IMAP reconnect attempt {_attempt + 1} failed: {e}", RNS.LOG_DEBUG)
                    self._imap = None
            else:
                try:
                    self._imap.noop()
                    return
                except Exception as e:
                    RNS.log(f"IMAP noop failed, will reconnect: {e}", RNS.LOG_DEBUG)
                    self._imap = None
        raise ConnectionError(f"IMAP connection failed after {max_retries} attempts")

    def _ensure_smtp(self):
        if self._smtp is not None:
            try:
                self._smtp.noop()
                return
            except Exception as e:
                RNS.log(f"SMTP noop failed, will reconnect: {e}", RNS.LOG_DEBUG)
                self._smtp = None
        self._smtp = self._create_smtp()

    def _cleanup_message(self, uid: bytes):
        try:
            self._imap.uid('copy', uid, self.PROCESSED_FOLDER)
            self._imap.uid('store', uid, '+FLAGS', '\\Deleted')
            self._imap.expunge()
        except Exception as e:
            RNS.log(f"Could not move message {uid} to {self.PROCESSED_FOLDER}, marking Seen: {e}", RNS.LOG_DEBUG)
            try:
                self._imap.uid('store', uid, '+FLAGS', '\\Seen')
            except Exception as e2:
                RNS.log(f"Could not mark message {uid} as Seen: {e2}", RNS.LOG_DEBUG)

    # ------------------------------------------------------------------
    #  Encode/decode -- padding, no base85 (MIME handles encoding)
    # ------------------------------------------------------------------

    def encode_payload(self, raw_packet: bytes) -> bytes:
        framed = HDLC.frame(raw_packet)
        return Padding.pad(framed, self.inner_size)

    def encode_batch(self, raw_packets: list) -> list:
        if not raw_packets:
            return []

        frames = [HDLC.frame(pkt) for pkt in raw_packets]
        max_data = Padding.max_payload(self.inner_size)

        payloads = []
        current_bin = b""

        for frame in frames:
            if len(current_bin) + len(frame) <= max_data:
                current_bin += frame
            else:
                if current_bin:
                    payloads.append(Padding.pad(current_bin, self.inner_size))
                current_bin = frame

        if current_bin:
            payloads.append(Padding.pad(current_bin, self.inner_size))

        return payloads

    def decode_payload(self, encoded: bytes) -> list:
        try:
            framed = Padding.unpad(encoded)
            return HDLC.deframe(framed)
        except Exception as e:
            RNS.log(f"Decode error on {self}: {e}", RNS.LOG_DEBUG)
            return []

    def __str__(self):
        return f"MailInterface[{self.name}]"
