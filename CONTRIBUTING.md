# Contributing

## Adding a New Transport

Every covert transport is a Reticulum custom interface built on the `CovertInterface` base class.
You implement four methods. The base class handles framing, padding, batching, rate limiting,
polling, and error recovery.

### Interface Contract

```python
from rns_covert.base import CovertInterface

class MyServiceInterface(CovertInterface):

    def __init__(self, owner, configuration):
        # Extract service-specific config before calling super().__init__()
        c = Interface.get_config_obj(configuration)
        self.api_key = c["api_key"]
        self.peer_id = c["peer_id"]
        super().__init__(owner, configuration)

    def start_transport(self):
        """
        Connect to the service. Authenticate.
        Called at startup and on each reconnection attempt.
        Raise an exception on failure.
        """
        self.client = SomeAPI(self.api_key)
        self.client.connect()

    def send_packet(self, encoded_data: bytes):
        """
        Send one encoded payload via the service.
        The payload is already padded to a fixed size.
        Raise an exception on failure.
        """
        self.client.send(to=self.peer_id, data=encoded_data)

    def poll_packets(self) -> list:
        """
        Check for incoming packets. Return a list of encoded payloads (bytes).
        Return an empty list if nothing new.
        Raise an exception on failure.
        """
        messages = self.client.get_new_messages(from_user=self.peer_id)
        return [msg.data for msg in messages]

    def stop_transport(self):
        """Clean shutdown."""
        self.client.disconnect()
```

### Reticulum Drop-in File

Create a file for `~/.reticulum/interfaces/` that exposes `interface_class`:

```python
from my_package.my_interface import MyServiceInterface
interface_class = MyServiceInterface
```

Reticulum loads external interfaces by executing this file and looking for `interface_class` in
the resulting global scope.

### Choosing a Service

Suitable transport services share these properties:

- Accessible in the target region (whitelisted, or too important to block)
- Programmable access (IMAP, REST API, WebDAV, or similar)
- Bidirectional (both sides can send and receive)
- Tolerates binary data or has text fields large enough for base64
- Widely used (traffic to this service does not stand out)
- Not aggressively rate-limited

For Russia specifically, high-value targets include:

- Yandex Mail (implemented)
- VKontakte (messages API, notes, wall posts)
- Yandex.Disk (WebDAV)
- Mail.ru (IMAP/SMTP)
- OK.ru (Odnoklassniki messages)
- Yandex.Cloud (S3-compatible object storage)

### Testing

At minimum, provide:

1. Unit tests for encoding roundtrips
2. Unit tests for message construction and extraction
3. End-to-end test using the filesystem-backed loopback (see `test_e2e.py`)

Run the test suite:

```
pip install -e ".[dev]"
pytest tests/ -v
python test_e2e.py
```

### Submitting Changes

1. Fork the repository
2. Create a branch for your work
3. Ensure all tests pass
4. Add an example configuration in `examples/configs/`
5. Update the transport table in `README.md`
6. Open a pull request

Every new transport makes the network harder to shut down.
