# Testing

## Unit Tests

No network access required. Tests encoding, HDLC framing, fixed-size padding,
email construction/extraction, and realistic content generation.

```
pip install -e ".[dev]"
pytest tests/ -v
```

91 tests covering:

- HDLC frame/deframe with all edge cases (flag bytes, escape bytes, multiple frames)
- Padding roundtrip, fixed-size verification, corruption detection
- Full pipeline: raw packet through HDLC + padding + base85 and back
- Blob and base64 encoder roundtrips
- Email construction with attachment, serialization through MIME, and extraction
- Fixed-size verification: all packet sizes produce identical attachment sizes
- Subject line, filename, and body text generation (variety, Cyrillic content)

## End-to-End Test

Launches two separate Reticulum processes connected through a filesystem-backed
`CovertInterface`. Tests the full interface lifecycle: announce, path discovery,
packet delivery, and cryptographic proof return. No email accounts required.

```
python test_e2e.py
```

Runs in approximately 5 seconds.

## Live Email Test

Tests actual packet delivery through Yandex Mail. Requires a Yandex Mail account.

1. Copy `test_live_email.py` (not included in the repository for credential safety)
   or write your own using the MailInterface configuration pattern in the README.

2. Configure your account credentials.

3. The test sends padded packets as emails to yourself, polls IMAP to receive them,
   decodes, and verifies byte-level integrity.

## Two-Node Live Test

The full integration test: two Reticulum processes communicating over Yandex Mail.

1. Configure credentials in a local test script (not committed to the repository).

2. Both nodes connect to Yandex Mail via IMAP/SMTP.

3. The server announces its destination (sent as an email).

4. The client discovers the server (by polling its inbox), sends an encrypted packet.

5. The server receives the packet and sends a cryptographic proof back.

6. Expected total time: 60-90 seconds with 3-second poll intervals.

Tested and verified with RTT of approximately 60-80 seconds over Yandex Mail
with 3-second poll intervals. In production with 30-second poll intervals,
steady-state RTT is approximately 60-120 seconds.
