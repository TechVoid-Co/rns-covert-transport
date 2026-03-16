# Changelog

## 0.1.0 (2026-03-16)

Initial release.

- `CovertInterface` base class with HDLC framing, fixed-size padding, packet batching,
  send rate limiting, poll loops, and automatic error recovery.
- `MailInterface` transport over standard IMAP/SMTP. Works with any provider.
  Tested with Yandex Mail and Gmail.
- Locale system for email camouflage: Russian (`ru`), English (`en`), and
  language-neutral (`neutral`) correspondence patterns.
- Blob encoding (raw binary attachment) and base64 encoding (text body fallback).
- Fixed-size packet padding. Every email attachment is identical in size.
- Message-ID tracking for single-account and dual-account operation.
- 62 unit tests, filesystem-backed end-to-end test.
- Verified over live Yandex Mail with full Reticulum encrypted packet roundtrip.
