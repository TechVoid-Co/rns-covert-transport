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

import imaplib
import smtplib
import email
import email.mime.base
import email.mime.multipart
import email.mime.text
import email.utils
import email.encoders
import os
import threading
import RNS
from RNS.Interfaces.Interface import Interface
from rns_covert.base import CovertInterface, HDLC, Padding
from rns_covert.encoding.strategies import get_encoder
from rns_covert.locale import get_locale, DEFAULT_LOCALE


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

        # Encoding and locale
        self.encoding_name = c.get("encoding", "blob")
        self.encoder       = get_encoder(self.encoding_name)
        locale_name        = c.get("locale", DEFAULT_LOCALE)
        self.locale        = get_locale(locale_name)

        # Connection state
        self._imap = None
        self._smtp_lock = threading.Lock()

        # Message-ID tracking (for single-account / self-to-self operation)
        self._sent_ids = set()
        self._processed_ids = set()

        super().__init__(owner, configuration)

    # ------------------------------------------------------------------
    #  Transport lifecycle
    # ------------------------------------------------------------------

    def start_transport(self):
        RNS.log(f"Connecting IMAP to {self.imap_host}:{self.imap_port}...", RNS.LOG_VERBOSE)
        self._imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        self._imap.login(self.account, self.password)
        self._imap.select(self.mailbox)

        if self.cleanup:
            try:
                self._imap.create(self.PROCESSED_FOLDER)
            except Exception:
                pass

        RNS.log(f"Verifying SMTP to {self.smtp_host}:{self.smtp_port}...", RNS.LOG_VERBOSE)
        smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)
        smtp.login(self.account, self.password)
        smtp.quit()

        RNS.log(f"Mail transport ready: {self.account} <-> {self.peer_address}", RNS.LOG_VERBOSE)

        # Mark all existing messages as processed
        self._scan_existing_messages()

    def _scan_existing_messages(self):
        try:
            status, data = self._imap.search(None, "ALL")
            if status == "OK" and data[0]:
                uids = data[0].split()
                for uid in uids:
                    try:
                        status, hdr_data = self._imap.fetch(
                            uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])"
                        )
                        if status == "OK" and hdr_data[0] and len(hdr_data[0]) > 1:
                            raw = hdr_data[0][1].decode("utf-8", errors="ignore")
                            for line in raw.splitlines():
                                if line.lower().startswith("message-id:"):
                                    self._processed_ids.add(line.split(":", 1)[1].strip())
                                    break
                    except Exception:
                        pass
                RNS.log(f"Marked {len(uids)} existing messages as processed", RNS.LOG_VERBOSE)
        except Exception:
            pass

    def send_packet(self, encoded_data: bytes):
        msg = self._build_email(encoded_data)

        msg_id = msg["Message-ID"]
        if msg_id:
            self._sent_ids.add(msg_id)

        with self._smtp_lock:
            smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)
            try:
                smtp.login(self.account, self.password)
                smtp.sendmail(self.account, [self.peer_address], msg.as_string())
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass

    def poll_packets(self) -> list:
        packets = []
        try:
            self._ensure_imap()
            self._imap.select(self.mailbox)

            status, data = self._imap.search(None, "ALL")
            if status != "OK" or not data[0]:
                return []

            for uid in data[0].split():
                try:
                    status, hdr_data = self._imap.fetch(
                        uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])"
                    )
                    if status != "OK":
                        continue

                    raw_header = hdr_data[0][1] if hdr_data[0] and len(hdr_data[0]) > 1 else b""
                    msg_id = ""
                    for line in raw_header.decode("utf-8", errors="ignore").splitlines():
                        if line.lower().startswith("message-id:"):
                            msg_id = line.split(":", 1)[1].strip()
                            break

                    if msg_id in self._sent_ids:
                        continue

                    if msg_id in self._processed_ids:
                        continue

                    payload = self._extract_packet(uid)
                    if payload is not None:
                        packets.append(payload)

                    self._processed_ids.add(msg_id)

                    if self.cleanup:
                        self._cleanup_message(uid)

                except Exception as e:
                    RNS.log(f"Error processing message {uid}: {e}", RNS.LOG_DEBUG)

        except imaplib.IMAP4.abort:
            RNS.log(f"IMAP connection reset on {self}, will reconnect", RNS.LOG_DEBUG)
            self._imap = None

        return packets

    def stop_transport(self):
        try:
            if self._imap:
                self._imap.close()
                self._imap.logout()
        except Exception:
            pass
        self._imap = None

    # ------------------------------------------------------------------
    #  Email construction
    # ------------------------------------------------------------------

    def _build_email(self, encoded_data: bytes):
        if self.encoding_name == "base64":
            return self._build_base64_email(encoded_data)
        else:
            return self._build_blob_email(encoded_data)

    def _build_blob_email(self, encoded_data: bytes):
        domain = self.account.split("@")[1]

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = self.account
        msg["To"] = self.peer_address
        msg["Subject"] = self.locale.generate_subject()
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
        domain = self.account.split("@")[1]

        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = self.account
        msg["To"] = self.peer_address
        msg["Subject"] = self.locale.generate_subject()
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid(domain=domain)

        b64_text = self.encoder.encode(encoded_data)
        msg.attach(email.mime.text.MIMEText(b64_text, "plain", "utf-8"))

        return msg

    # ------------------------------------------------------------------
    #  Packet extraction
    # ------------------------------------------------------------------

    def _extract_packet(self, msg_id: bytes) -> bytes:
        status, data = self._imap.fetch(msg_id, "(RFC822)")
        if status != "OK":
            return None

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        if self.encoding_name == "base64":
            return self._extract_base64(msg)
        else:
            return self._extract_blob(msg)

    def _extract_blob(self, msg) -> bytes:
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                payload = part.get_payload(decode=True)
                if payload and len(payload) > 0:
                    try:
                        return self.encoder.decode(payload)
                    except Exception:
                        continue
        return None

    def _extract_base64(self, msg) -> bytes:
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                text = part.get_payload(decode=True)
                if text:
                    try:
                        return self.encoder.decode(text.decode("utf-8", errors="ignore"))
                    except Exception:
                        continue
        return None

    # ------------------------------------------------------------------
    #  IMAP helpers
    # ------------------------------------------------------------------

    def _ensure_imap(self):
        if self._imap is None:
            self._imap = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            self._imap.login(self.account, self.password)
            self._imap.select(self.mailbox)
        else:
            try:
                self._imap.noop()
            except Exception:
                self._imap = None
                self._ensure_imap()

    def _cleanup_message(self, msg_id: bytes):
        try:
            self._imap.copy(msg_id, self.PROCESSED_FOLDER)
            self._imap.store(msg_id, "+FLAGS", "\\Deleted")
            self._imap.expunge()
        except Exception:
            try:
                self._imap.store(msg_id, "+FLAGS", "\\Seen")
            except Exception:
                pass

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
