# Security

## Threat Model

This project assumes the following adversary capabilities:

- Deep packet inspection (DPI) on all network traffic
- Ability to block arbitrary protocols, IP addresses, and domains
- Access to whitelist/blacklist DNS filtering
- Potential access to email server metadata (headers, timestamps, sender/recipient)
- Ability to compel domestic service providers to cooperate

The project does NOT protect against:

- An adversary with direct access to the email account credentials
- An adversary with root access to the endpoint device
- An adversary performing targeted surveillance of a specific individual
  with full access to the mail server's database
- Rubber-hose cryptanalysis

## What Is Encrypted

All Reticulum traffic is encrypted end-to-end before it reaches this transport layer.
This project never sees plaintext. The encryption is handled entirely by Reticulum:

- X25519 key exchange
- AES-256 symmetric encryption
- Ed25519 signatures
- Ephemeral keys with forward secrecy

The attachment content is an opaque binary blob, indistinguishable from random data.

## What Is Visible to an Observer

An adversary monitoring network traffic or with access to the mail server can observe:

- That two email accounts are exchanging messages
- The timing and frequency of those messages
- That each message contains an attachment of a fixed size
- Standard email metadata (headers, timestamps, routing information)

They cannot observe:

- The content of the Reticulum packets
- Which Reticulum destinations are communicating
- The type of traffic (announce, data, proof)
- The actual payload size (fixed-size padding prevents this)

## Operational Security Recommendations

1. Use dedicated accounts created solely for this purpose. Never use personal email.

2. Create accounts over Tor or a VPN that is not yet blocked in your region.

3. Do not access the transport accounts from a web browser or standard email client.
   The only access should be through this interface.

4. Use two separate accounts, one per node. Single-account (self-to-self) operation
   is supported but creates a more unusual traffic pattern.

5. Keep `max_sends_per_hour` at 30 or below. Sustained high-frequency email exchange
   between two accounts will trigger anti-spam systems and attract attention.

6. Run with `cleanup = yes` to move processed emails out of the inbox. A mailbox
   accumulating hundreds of identically-sized attachments is conspicuous.

7. Periodically rotate accounts. Do not use the same pair of accounts indefinitely.

8. Ensure the endpoint device is secured. Full-disk encryption is strongly recommended.

9. Be aware that Yandex is a Russian company subject to Russian law. The mail server
   operator can be compelled to provide account data. The encryption protects content,
   but metadata (who is communicating with whom, and when) is visible to the operator.

## Reporting Vulnerabilities

If you discover a security vulnerability in this project, do not open a public issue.
Contact the maintainers directly through an encrypted channel. Details in the repository's
security policy configuration.
