#!/usr/bin/env python3
"""Generate a private VAPID P-256 PEM file without printing its material."""
import argparse
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New private PEM file; existing files are refused")
    args = parser.parse_args()
    data = ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    try:
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except OSError:
        parser.exit(1, "A new private key file could not be created; no existing file was changed.\n")
    print("Private VAPID key created with mode 0600. Supply it through the installation secret store; do not publish it.")


if __name__ == "__main__":
    main()
