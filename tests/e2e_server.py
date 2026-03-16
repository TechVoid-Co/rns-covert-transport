#!/usr/bin/env python3
"""E2E test: server node. Run via test_e2e.py, not directly."""
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
        self._outbox = os.path.join(WORKSPACE, "server_to_client")
        self._inbox  = os.path.join(WORKSPACE, "client_to_server")
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
        return "FileMailInterface[server]"


# ── Setup ──
config_dir = os.path.join(WORKSPACE, "config_server")
os.makedirs(config_dir, exist_ok=True)
with open(os.path.join(config_dir, "config"), "w") as f:
    f.write("[reticulum]\n  enable_transport = no\n  share_instance = no\n\n[interfaces]\n")

RNS.loglevel = int(os.environ.get("RNS_LOGLEVEL", "2"))
reticulum = RNS.Reticulum(config_dir)

config_if = {"name": "FileLink-server", "poll_interval": "0.5", "bitrate": "10000", "batch_window": "1", "max_sends_per_hour": "3600"}
iface = FileMailInterface(RNS.Transport, config_if)
reticulum._add_interface(iface, ifac_netname=None, ifac_netkey=None)

# ── Create echo destination ──
identity = RNS.Identity()
dest = RNS.Destination(identity, RNS.Destination.IN, RNS.Destination.SINGLE, "e2e_test", "echo", "request")
dest.set_proof_strategy(RNS.Destination.PROVE_ALL)

received = threading.Event()
msg_data = [None]

def on_packet(message, packet):
    msg_data[0] = message.decode("utf-8")
    RNS.log(f"Server received: {msg_data[0]}", RNS.LOG_NOTICE)
    received.set()

dest.set_packet_callback(on_packet)

# Write raw hex hash for client
with open(os.path.join(WORKSPACE, "server_hash.txt"), "w") as f:
    f.write(dest.hash.hex())

dest.announce()
RNS.log(f"Server announced: {dest.hash.hex()}", RNS.LOG_NOTICE)

# Wait for message
if received.wait(30):
    result = {"status": "ok", "message": msg_data[0]}
else:
    result = {"status": "timeout"}

with open(os.path.join(WORKSPACE, "server_result.json"), "w") as f:
    json.dump(result, f)

time.sleep(3)
reticulum.exit_handler()
