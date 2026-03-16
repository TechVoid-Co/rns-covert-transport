#!/usr/bin/env python3
"""E2E test: client node. Run via test_e2e.py, not directly."""
import sys
import os
import time
import threading
import glob
import json

sys.path.insert(0, os.environ["PROJECT_DIR"])
WORKSPACE = os.environ["WORKSPACE"]

import RNS  # noqa: E402
from rns_covert.base import CovertInterface  # noqa: E402

class FileMailInterface(CovertInterface):
    """Sends packets as files; polls a directory for incoming."""
    def __init__(self, owner, configuration):
        self._outbox = os.path.join(WORKSPACE, "client_to_server")
        self._inbox  = os.path.join(WORKSPACE, "server_to_client")
        self._counter = 0
        super().__init__(owner, configuration)

    def start_transport(self):
        os.makedirs(self._outbox, exist_ok=True)
        os.makedirs(self._inbox, exist_ok=True)

    def send_packet(self, encoded_data: bytes):
        self._counter += 1
        path = os.path.join(self._outbox, f"pkt_{time.time():.6f}_{self._counter}.bin")
        with open(path, "wb") as f:
            f.write(encoded_data)

    def poll_packets(self) -> list:
        packets = []
        for fpath in sorted(glob.glob(os.path.join(self._inbox, "pkt_*.bin"))):
            try:
                with open(fpath, "rb") as f:
                    packets.append(f.read())
                os.unlink(fpath)
            except Exception:
                pass
        return packets

    def stop_transport(self):
        pass

    def __str__(self):
        return "FileMailInterface[client]"


# ── Setup ──
config_dir = os.path.join(WORKSPACE, "config_client")
os.makedirs(config_dir, exist_ok=True)
with open(os.path.join(config_dir, "config"), "w") as f:
    f.write("[reticulum]\n  enable_transport = no\n  share_instance = no\n\n[interfaces]\n")

RNS.loglevel = int(os.environ.get("RNS_LOGLEVEL", "2"))
reticulum = RNS.Reticulum(config_dir)

config_if = {"name": "FileLink-client", "poll_interval": "0.5", "bitrate": "10000", "batch_window": "1", "max_sends_per_hour": "3600"}
iface = FileMailInterface(RNS.Transport, config_if)
reticulum._add_interface(iface, ifac_netname=None, ifac_netkey=None)

# ── Wait for server hash ──
hash_file = os.path.join(WORKSPACE, "server_hash.txt")
deadline = time.time() + 10
while not os.path.exists(hash_file):
    if time.time() > deadline:
        with open(os.path.join(WORKSPACE, "client_result.json"), "w") as f:
            json.dump({"status": "error", "message": "server hash not found"}, f)
        reticulum.exit_handler()
        sys.exit(1)
    time.sleep(0.3)

with open(hash_file) as f:
    server_hash = bytes.fromhex(f.read().strip())

RNS.log(f"Client targeting server {server_hash.hex()}", RNS.LOG_NOTICE)

# ── Wait for path discovery ──
deadline = time.time() + 20
requested = False
while not RNS.Transport.has_path(server_hash):
    if time.time() > deadline:
        with open(os.path.join(WORKSPACE, "client_result.json"), "w") as f:
            json.dump({"status": "error", "message": "path not discovered"}, f)
        reticulum.exit_handler()
        sys.exit(1)
    if not requested and time.time() > deadline - 15:
        RNS.Transport.request_path(server_hash)
        requested = True
    time.sleep(0.5)

RNS.log("Path discovered!", RNS.LOG_NOTICE)

# ── Send packet ──
server_identity = RNS.Identity.recall(server_hash)
out_dest = RNS.Destination(
    server_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
    "e2e_test", "echo", "request",
)

test_msg = "Привет из-за стены цензуры!"
packet = RNS.Packet(out_dest, test_msg.encode("utf-8"))
receipt = packet.send()

proof_event = threading.Event()
rtt = [None]

if receipt:
    def on_delivered(r):
        rtt[0] = r.get_rtt()
        proof_event.set()
    receipt.set_timeout(20)
    receipt.set_delivery_callback(on_delivered)

proof_event.wait(20)

result = {
    "status": "ok" if proof_event.is_set() else "no_proof",
    "message": test_msg,
    "rtt": rtt[0],
}
with open(os.path.join(WORKSPACE, "client_result.json"), "w") as f:
    json.dump(result, f)

time.sleep(1)
reticulum.exit_handler()
