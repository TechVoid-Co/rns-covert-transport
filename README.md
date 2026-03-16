# rns-covert-transport

Reticulum Network Stack interfaces for censorship-resistant communication over everyday services.

When internet access is restricted to a whitelist of state-approved services, when VPNs are blocked,
when DPI is deployed at every ISP, and when the authorities hold a kill switch for the entire network,
people still need a way to communicate securely. This project provides that way.

rns-covert-transport implements custom interfaces for the [Reticulum Network Stack](https://reticulum.network/)
that tunnel encrypted packets through ordinary services -- services that censors cannot easily block
without disrupting the economy.

Inspired by [Delta Chat](https://delta.chat/), which turns email into a messenger.

## How It Works

Reticulum encrypts all traffic by default. No unencrypted packets can exist on the network. Forward secrecy
is standard. Packets carry no source addresses.

This project takes those encrypted packets and disguises them as normal service traffic:

```
Reticulum packet (encrypted, fixed-size, no source address)
    -> HDLC frame
    -> pad to fixed size (every packet identical length)
    -> send as email attachment / API message / cloud file
    -> peer polls, extracts, unpads, deframes
    -> Reticulum processes the packet
```

An observer sees ordinary emails between two accounts. The attachments are opaque binary blobs,
indistinguishable from compressed archives, scanned documents, or backup files. Every attachment is
exactly the same size. Subject lines, filenames, and body text are generated from pools of locale-
appropriate correspondence patterns.

## Available Transports

| Transport | Protocol | Tested With | Status |
|---|---|---|---|
| `MailInterface` | IMAP/SMTP | Yandex Mail, Gmail | Working, tested |

The `MailInterface` works with any email provider that supports standard IMAP and SMTP over SSL.
Additional transports (VKontakte, Yandex.Disk, cloud storage APIs) can be built on the same base class.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Requirements

- Python 3.8+
- [Reticulum Network Stack](https://reticulum.network/) (`rns` package)
- Two email accounts on the target service (one per node)

## Installation

```
pip install rns-covert-transport
```

Or from source:

```
git clone https://github.com/TechVoid-Co/rns-covert-transport
cd rns-covert-transport
pip install -e .
```

## Quick Start

### 1. Create two email accounts

Use any provider with IMAP/SMTP support. Dedicated accounts only -- do not use personal email.

Tested providers:
- Yandex Mail (mail.yandex.ru) -- enable IMAP in settings
- Gmail (gmail.com) -- generate app password at myaccount.google.com/apppasswords
- Mail.ru, Outlook, or any standard IMAP/SMTP server

### 2. Install the interface

```
mkdir -p ~/.reticulum/interfaces
cp rns_covert/interfaces/MailInterface.py ~/.reticulum/interfaces/
```

### 3. Configure Node A

Edit `~/.reticulum/config`:

```ini
[reticulum]
  enable_transport = no
  share_instance = yes

[interfaces]
  [[Mail Transport]]
    type = MailInterface
    enabled = yes
    account = node_a@yandex.ru
    password = app_password_here
    peer_address = node_b@yandex.ru
    imap_host = imap.yandex.ru
    smtp_host = smtp.yandex.ru
    locale = ru
    encoding = blob
    inner_size = 1280
    poll_interval = 30
    max_sends_per_hour = 30
    batch_window = 5
```

### 4. Configure Node B

Same configuration with `account` and `peer_address` swapped.

### 5. Start both nodes

```
rnsd -v
```

Use [Sideband](https://github.com/markqvist/sideband), [LXMF](https://github.com/markqvist/lxmf),
`rncp`, `rnprobe`, or any Reticulum application to communicate.

## Configuration Reference

| Option | Default | Description |
|---|---|---|
| `account` | (required) | Email address for this node |
| `password` | (required) | App-specific password |
| `peer_address` | (required) | Email address of the peer node |
| `imap_host` | (required) | IMAP server hostname |
| `smtp_host` | (required) | SMTP server hostname |
| `imap_port` | `993` | IMAP port (SSL) |
| `smtp_port` | `465` | SMTP port (SSL) |
| `locale` | `ru` | Email camouflage language: `ru`, `en`, `neutral` |
| `encoding` | `blob` | `blob` (binary attachment) or `base64` (text body) |
| `inner_size` | `1280` | Fixed payload size in bytes. Both peers must match. |
| `poll_interval` | `30` | Seconds between inbox checks |
| `max_sends_per_hour` | `30` | Rate limit for outbound emails |
| `batch_window` | `5` | Seconds to collect packets before sending one email |
| `cleanup` | `yes` | Move processed emails to subfolder |
| `retry_delay` | `60` | Seconds before reconnection attempt after failure |

### Locale Reference

| Locale | Language | Subject/filename examples |
|---|---|---|
| `ru` | Russian | "Счёт-фактура №А-4821", "договор_1234.docx" |
| `en` | English | "Invoice #4821", "contract_1234.docx" |
| `neutral` | ASCII-only | "Re: #4821", "doc_1234.pdf" |

### Provider Quick Reference

| Provider | imap_host | smtp_host | Notes |
|---|---|---|---|
| Yandex | imap.yandex.ru | smtp.yandex.ru | Enable IMAP in settings. 500 emails/day. |
| Gmail | imap.gmail.com | smtp.gmail.com | App password required. 500 emails/day. |
| Mail.ru | imap.mail.ru | smtp.mail.ru | Enable IMAP in settings. |
| Outlook | outlook.office365.com | smtp.office365.com | App password required. Port 587 for SMTP. |

## Architecture

```
rns_covert/
  base.py                   Base class for all covert interfaces.
                            HDLC framing, fixed-size padding, packet
                            batching, rate limiting, poll loops,
                            error recovery.

  locale.py                 Email camouflage locales (ru, en, neutral).

  encoding/
    strategies.py           Encoding strategies (blob, base64).

  interfaces/
    mail.py                 IMAP/SMTP transport implementation.
    MailInterface.py        Drop-in for ~/.reticulum/interfaces/
```

### Security Properties

**Provided by Reticulum (not this project):**
- End-to-end encryption (X25519 + AES-256)
- Forward secrecy (ephemeral keys)
- No source addresses on any packet
- Unforgeable delivery proofs

**Provided by this project:**
- Fixed-size padding: every email attachment is identical in size regardless of payload.
  Traffic analysis based on packet length is not possible.
- Locale-appropriate camouflage: email subjects, filenames, and body text are drawn from
  pools of realistic correspondence patterns for the configured language.
- Rate limiting and batching: traffic patterns are controlled to stay within provider limits
  and avoid triggering spam detection.
- Idle silence: no traffic is generated when there is nothing to send.

**Not provided (known limitations):**
- Timing analysis is possible for an adversary with access to both mail servers.
- Email metadata (headers, timestamps, routing) is visible to the mail server operator.
- Sustained high-volume communication between two accounts is detectable regardless of content.

See [SECURITY.md](SECURITY.md) for the full threat model.

## License

Apache-2.0 License. See [LICENSE](LICENSE).

## Related Projects

- [Reticulum](https://reticulum.network/) -- Cryptography-based networking stack
- [Delta Chat](https://delta.chat/) -- Messenger over email
- [LXMF](https://github.com/markqvist/lxmf) -- Delay-tolerant messaging over Reticulum
- [Sideband](https://github.com/markqvist/sideband) -- Reticulum messenger application
