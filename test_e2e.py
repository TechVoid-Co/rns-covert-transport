#!/usr/bin/env python3
"""
End-to-end test: two Reticulum nodes communicating via CovertInterface.

Uses filesystem-backed transport (files in a shared temp directory)
to simulate the same encode → send → poll → decode pipeline that
the Yandex Mail interface uses.

Usage:
    python test_e2e.py                    # normal
    RNS_LOGLEVEL=6 python test_e2e.py     # verbose

No accounts needed. Runs in ~15 seconds.
"""

import sys
import os
import time
import json
import shutil
import subprocess
import tempfile

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR   = os.path.join(PROJECT_DIR, "tests")
TIMEOUT     = 40


def main():
    print("=" * 60)
    print("  RNS Covert Transport -- End-to-End Test")
    print("  Two nodes ↔ filesystem-backed CovertInterface")
    print("=" * 60)
    print()

    workspace = tempfile.mkdtemp(prefix="rns_e2e_")
    env = os.environ.copy()
    env["PROJECT_DIR"] = PROJECT_DIR
    env["WORKSPACE"]   = workspace
    env.setdefault("RNS_LOGLEVEL", "2")

    server_proc = None
    client_proc = None

    try:
        # ── Start server ──
        print("[1/4] Starting server node...")
        server_proc = subprocess.Popen(
            [sys.executable, os.path.join(TESTS_DIR, "e2e_server.py")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        time.sleep(3)  # Let server announce

        # ── Start client ──
        print("[2/4] Starting client node...")
        client_proc = subprocess.Popen(
            [sys.executable, os.path.join(TESTS_DIR, "e2e_client.py")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )

        print("[3/4] Waiting for packet exchange...")

        try:
            client_proc.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            client_proc.kill()

        try:
            server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_proc.kill()

        server_log = server_proc.stdout.read().decode("utf-8", errors="replace")
        client_log = client_proc.stdout.read().decode("utf-8", errors="replace")

        # ── Results ──
        print("[4/4] Collecting results...")
        print()

        server_ok = False
        client_ok = False
        rtt = None

        sr_path = os.path.join(workspace, "server_result.json")
        cr_path = os.path.join(workspace, "client_result.json")

        if os.path.exists(sr_path):
            with open(sr_path) as f:
                sr = json.load(f)
            server_ok = sr.get("status") == "ok"
            if server_ok:
                print(f"  Server received: '{sr.get('message')}'")
            else:
                print(f"  Server status: {sr.get('status')} -- {sr.get('message','')}")

        if os.path.exists(cr_path):
            with open(cr_path) as f:
                cr = json.load(f)
            client_ok = cr.get("status") == "ok"
            rtt = cr.get("rtt")
            if client_ok and rtt:
                print(f"  Client got proof -- RTT: {rtt:.3f}s")
            else:
                print(f"  Client status: {cr.get('status')} -- {cr.get('message','')}")

        print()
        print("=" * 60)

        if server_ok:
            print("  ✅  TEST PASSED")
            print()
            print("  Two separate Reticulum processes communicated")
            print("  through a CovertInterface (filesystem-backed).")
            print()
            print("  Packet path:")
            print("    Client RNS → HDLC frame → base85 encode →")
            print("    write file → [disk] → read file →")
            print("    base85 decode → HDLC deframe → Server RNS")
            if client_ok:
                print()
                print("  Proof path back confirmed (RTT measured).")
            print()
            print("  The Yandex Mail adapter uses the exact same")
            print("  pipeline -- just IMAP/SMTP instead of files.")
        else:
            print("  ❌  TEST FAILED")
            if env.get("RNS_LOGLEVEL") == "2":
                print()
                print("  Try: RNS_LOGLEVEL=6 python test_e2e.py")
            print()
            print("  ── Server log (last 20 lines) ──")
            for line in server_log.strip().split("\n")[-20:]:
                print(f"    {line}")
            print()
            print("  ── Client log (last 20 lines) ──")
            for line in client_log.strip().split("\n")[-20:]:
                print(f"    {line}")

        print("=" * 60)
        return server_ok

    finally:
        for proc in [server_proc, client_proc]:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
