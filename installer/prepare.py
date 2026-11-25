#!/usr/bin/env python3
"""Prepare a machine-specific Yeet installer on a workstation."""

import argparse
import curses
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

from provision import SetupError, run, validate_network
from setup import Cancelled, Console, static_addresses

ROOT = Path(__file__).resolve().parents[1]
KEY_DOCS = "https://tailscale.com/kb/1085/auth-keys"


def validate_machine(m):
    if set(m) != {"hostname", "hostId", "sshPublicKey", "network"}:
        raise ValueError(
            "The public profile accepts only hostname, hostId, sshPublicKey and network."
        )
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", m["hostname"]):
        raise ValueError("Use a lowercase DNS hostname.")
    if not re.fullmatch("[0-9a-f]{8}", m["hostId"]) or int(m["hostId"], 16) == 0:
        raise ValueError("Use an eight-digit, nonzero hexadecimal ZFS host ID.")
    pub = m["sshPublicKey"]
    if "\n" in pub or not pub.startswith(("ssh-", "ecdsa-", "sk-")):
        raise ValueError("Choose one OpenSSH public key.")
    run("ssh-keygen", "-lf", "/dev/stdin", stdin=pub + "\n")
    validate_network(m["network"])


def wizard(s):
    ui = Console(s, escape_hint="Cancel")
    name = ui.field(
        "MACHINE",
        "Name your host",
        "This is the name you will connect to from your workstation.",
        "yeet-home",
    )
    pub = (
        Path(
            ui.field(
                "ACCESS",
                "Your SSH public key",
                "Choose the public key allowed to open the installer.",
                "~/.ssh/id_ed25519.pub",
            )
        )
        .expanduser()
        .read_text()
        .strip()
    )
    static = ui.choose(
        "NETWORK",
        "How does this host get online?",
        "These settings apply on first boot and after installation.",
        ["Automatic (DHCP)", "Static IPv4"],
    )
    net = {"mode": "dhcp"}
    if static:
        nic = ui.field(
            "NETWORK",
            "Which network adapter?",
            "Leave blank to select the adapter on the live machine, or enter a known MAC address or interface name.",
        )
        net = {
            "mode": "static",
            **({("mac" if ":" in nic else "interface"): nic} if nic else {}),
            **static_addresses(ui),
        }
    machine = dict(
        hostname=name, hostId=secrets.token_hex(4), sshPublicKey=pub, network=net
    )
    validate_machine(machine)
    key = ui.field(
        "TAILSCALE",
        "Tailscale enrollment key",
        "Paste a single-use, preauthorized, non-ephemeral tag:catch key. Create one using "
        + KEY_DOCS,
        secret=True,
    )
    output = ui.field(
        "BUILD",
        "Save your private installer",
        "Write this image to USB or attach it as a virtual CD.",
        name + ".iso",
    )
    return machine, key, Path(output).expanduser()


def build(machine, key, output, source):
    validate_machine(machine)
    if not key.startswith("tskey-auth-"):
        raise ValueError("Supply a Tailscale auth key.")
    if output.exists():
        raise ValueError(f"{output} already exists. Choose a new output filename.")
    output = output.absolute()
    with tempfile.TemporaryDirectory(prefix=".yeet-build-", dir=output.parent) as tmp:
        directory = Path(tmp)
        spec = directory / "machine.json"
        spec.write_text(json.dumps(machine))
        # getFlake reads only the project, and the derivation receives only the
        # public machine specification. The private key is written after Nix exits.
        env = dict(os.environ, YEET_SOURCE=source, YEET_PROFILE=str(spec))
        expr = '(builtins.getFlake (builtins.getEnv "YEET_SOURCE")).lib.installerISO (builtins.fromJSON (builtins.readFile (builtins.getEnv "YEET_PROFILE")))'
        base = directory / "base"
        subprocess.run(
            [
                "nix",
                "--extra-experimental-features",
                "nix-command flakes",
                "build",
                "--impure",
                "--expr",
                expr,
                "-o",
                str(base),
            ],
            env=env,
            check=True,
        )
        iso = base / "iso/yeet-os-installer.iso"
        private = directory / "private"
        private.mkdir(mode=0o700)
        (private / "catch.key").write_text(key.strip() + "\n")
        subprocess.run(
            [
                "xorriso",
                "-indev",
                str(iso),
                "-outdev",
                str(directory / "finished.iso"),
                "-map",
                str(private),
                "/yeet-private",
                "-boot_image",
                "any",
                "replay",
                "-commit",
                "-end",
            ],
            check=True,
        )
        (directory / "finished.iso").chmod(0o600)
        os.link(directory / "finished.iso", output)
    print(
        f"{output}\nBoot the image, then connect with ssh -t root@{machine['hostname']} (use the assigned tailnet name if it differs)."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=Path, help="public machine JSON for a noninteractive build"
    )
    parser.add_argument("--auth-key-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source", default=str(ROOT), help="Yeet OS flake")
    args = parser.parse_args()
    os.umask(0o077)
    if args.profile:
        if not args.output or not args.auth_key_file:
            parser.error("--profile requires --output and --auth-key-file")
        machine = json.loads(args.profile.read_text())
        key = args.auth_key_file.read_text().strip()
        output = args.output
    else:
        machine, key, output = curses.wrapper(wizard)
    build(machine, key, output, args.source)


if __name__ == "__main__":
    try:
        main()
    except (Cancelled, KeyboardInterrupt):
        sys.exit(130)
    except (
        SetupError,
        OSError,
        ValueError,
        KeyError,
        subprocess.CalledProcessError,
    ) as err:
        sys.exit(str(err))
