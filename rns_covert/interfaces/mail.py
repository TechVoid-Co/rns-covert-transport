"""
MailInterface -- Reticulum transport over standard IMAP/SMTP email.

Works with any email provider that supports IMAP and SMTP over SSL.

Key design decisions for reliability:
  - All IMAP operations use UID commands (stable across expunge)
  - Startup records existing UIDs without marking them processed,
    so in-flight messages from before startup are not lost
  - Cleanup (delete/move) happens AFTER all messages in a poll
    cycle are processed, never mid-loop (avoids UID shifts)
  - Sent Message-IDs are tracked to skip own messages in
    single-account operation
"""

import imaplib
import smtplib
import email
import email.mime.base
import email.mime.multipart
import email.mime.text
import email.utils
import email.encoders
import threading
import RNS
from RNS.Interfaces.Interface import Interface
from rns_covert.base import CovertInterface, HDLC, Padding
from rns_covert.encoding.strategies import get_encoder
from rns_covert.locale import get_locale, DEFAULT_LOCALE


class MailInterface(CovertInterface):
    """
    Reticulum custom interface -- transport over standard IMAP/SMTP.
    """

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

        # Optional
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

        # UID-based tracking.
        # _seen_uids: set of IMAP UIDs we have already seen (at startup
        # or after processing). New messages have UIDs not in this set.
        self._seen_uids = set()

        # _sent_message_ids: set of Message-ID header values for emails
        # we sent. Used to skip our own messages in single-account mode.
        self._sent_message_ids = set()

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

        # Record all current UIDs so we only process NEW messages.
        # This is fast -- just a UID SEARCH, no header fetching.
        self._snapshot_existing_uids()

    def _snapshot_existing_uids(self):
        """Record UIDs of all messages currently in the inbox."""
        try:
            status, data = self._imap.uid("SEARCH", None, "ALL")
            if status == "OK" and data[0]:
                for uid in data[0].split():
                    self._seen_uids.add(uid)
            count = len(self._seen_uids)
            RNS.log(f"Recorded {count} existing message UIDs", RNS.LOG_VERBOSE)
        except Exception as e:
            RNS.log(f"Could not snapshot existing UIDs: {e}", RNS.LOG_WARNING)

    # ------------------------------------------------------------------
    #  Send
    # ------------------------------------------------------------------

    def send_packet(self, encoded_data: bytes):
        msg = self._build_email(encoded_data)

        # Track Message-ID so we skip our own mail when polling
        msg_id = msg["Message-ID"]
        if msg_id:
            self._sent_message_ids.add(msg_id)

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

    # ------------------------------------------------------------------
    #  Poll
    # ------------------------------------------------------------------

    def poll_packets(self) -> list:
        """
        Check inbox for new messages. Uses IMAP UIDs for stability.

        Steps:
          1. UID SEARCH ALL -- get current UIDs
          2. new_uids = current - _seen_uids
          3. For each new UID: fetch, check not sent by self, extract
          4. Mark all new UIDs as seen
          5. Batch cleanup AFTER processing (no mid-loop expunge)
        """
        packets = []
        uids_to_cleanup = []

        try:
            self._ensure_imap()
            self._imap.select(self.mailbox)

            # Step 1: get all current UIDs
            status, data = self._imap.uid("SEARCH", None, "ALL")
            if status != "OK" or not data[0]:
                return []

            current_uids = set(data[0].split())

            # Step 2: find new UIDs
            new_uids = current_uids - self._seen_uids
            if not new_uids:
                return []

            RNS.log(f"{self}: {len(new_uids)} new message(s) to process", RNS.LOG_DEBUG)

            # Step 3: process each new message
            for uid in sorted(new_uids):
                try:
                    # Fetch full message
                    status, msg_data = self._imap.uid("FETCH", uid, "(RFC822)")
                    if status != "OK" or not msg_data[0]:
                        self._seen_uids.add(uid)
                        continue

                    raw_email = msg_data[0][1]
                    parsed = email.message_from_bytes(raw_email)

                    # Check if we sent this ourselves (single-account mode)
                    msg_id = parsed.get("Message-ID", "")
                    if msg_id in self._sent_message_ids:
                        self._seen_uids.add(uid)
                        if self.cleanup:
                            uids_to_cleanup.append(uid)
                        continue

                    # Extract packet
                    payload = self._extract_from_parsed(parsed)
                    if payload is not None:
                        packets.append(payload)

                    # Mark as seen regardless of extraction success
                    self._seen_uids.add(uid)

                    if self.cleanup:
                        uids_to_cleanup.append(uid)

                except Exception as e:
                    RNS.log(f"Error processing UID {uid}: {e}", RNS.LOG_DEBUG)
                    # Still mark as seen so we don't retry forever
                    self._seen_uids.add(uid)

            # Step 5: batch cleanup AFTER all processing
            if uids_to_cleanup:
                self._batch_cleanup(uids_to_cleanup)

        except imaplib.IMAP4.abort:
            RNS.log(f"IMAP connection reset on {self}, will reconnect", RNS.LOG_DEBUG)
            self._imap = None
        except Exception as e:
            RNS.log(f"Poll error on {self}: {e}", RNS.LOG_DEBUG)

        return packets

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

    def _extract_from_parsed(self, msg) -> bytes:
        """Extract packet payload from a parsed email message."""
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

    def _batch_cleanup(self, uids: list):
        """
        Move/delete processed messages AFTER all polling is done.
        Uses UID commands so IDs remain stable throughout.
        """
        try:
            for uid in uids:
                try:
                    if self.cleanup:
                        self._imap.uid("COPY", uid, self.PROCESSED_FOLDER)
                    self._imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
                except Exception:
                    pass

            # Single expunge at the end
            self._imap.expunge()

        except Exception as e:
            RNS.log(f"Cleanup error on {self}: {e}", RNS.LOG_DEBUG)

    def stop_transport(self):
        try:
            if self._imap:
                self._imap.close()
                self._imap.logout()
        except Exception:
            pass
        self._imap = None

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
