#!/usr/bin/env python3
"""Sign the fork's public update catalog; never send a signing key to a mirror."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPOSITORY = "Dingtaiqi/skyward"


def public_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def catalog(images, tag, key, trusted_key):
    if public_bytes(key) != trusted_key:
        raise ValueError("the signing key does not match resources/update-key.pub")
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", tag):
        raise ValueError("the update tag must be v followed by a semantic version")
    assets, devices = [], set()
    for path in sorted(images.glob("*.zip")):
        match = re.fullmatch(r"skyward-" + re.escape(tag) + r"-([a-z0-9_]+)-[A-Za-z0-9_.-]+\.zip", path.name)
        if not match or "diagnostic" in path.name:
            raise ValueError(f"not a release image: {path.name}")
        device = match[1]
        if device in devices:
            raise ValueError(f"multiple builds for {device}; choose one tested variant")
        devices.add(device)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        size = path.stat().st_size
        if not size:
            raise ValueError(f"empty update: {path.name}")
        link = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{path.name}"
        assets.append(dict(name=path.name, size=size, digest="sha256:" + digest.hexdigest(), url=link, browser_download_url=link))
    if not assets:
        raise ValueError("no recovery ZIPs to publish")
    payload = json.dumps(dict(schema=1, repository=REPOSITORY, tag_name=tag, assets=assets), separators=(",", ":"), sort_keys=True).encode()
    return dict(payload=base64.b64encode(payload).decode(), signature=base64.b64encode(key.sign(payload)).decode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--public-key", type=Path, default=Path("resources/update-key.pub"))
    parser.add_argument("--init-key", type=Path, help="create a local private seed outside the repository, once")
    args = parser.parse_args()
    if args.init_key:
        key = Ed25519PrivateKey.generate()
        seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        args.init_key.parent.mkdir(parents=True, exist_ok=True)
        with args.init_key.open("xb") as private:
            os.chmod(args.init_key, 0o600)
            private.write(base64.b64encode(seed) + b"\n")
        args.public_key.write_bytes(base64.b64encode(public_bytes(key)) + b"\n")
        print("Created a local signing key and its public verification key; private bytes were not displayed.")
        return
    if not args.images or not args.tag or not args.out:
        parser.error("--images, --tag and --out are required when signing")
    encoded = os.environ.get("SKYWARD_UPDATE_SIGNING_KEY", "")
    if not encoded:
        parser.error("configure the SKYWARD_UPDATE_SIGNING_KEY Actions secret; unsigned updates are not published")
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(encoded, validate=True))
    trusted = base64.b64decode(args.public_key.read_text().strip(), validate=True)
    signed = catalog(args.images, args.tag, key, trusted)
    args.out.write_text(json.dumps(signed, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Signed catalog for {args.tag}; no key material was printed.")


if __name__ == "__main__":
    main()
