"""Disk installation and enrollment shared by the console and seed-file flows."""

import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import socket
import subprocess
import tempfile

from system_build import FACTS, build_system


class SetupError(Exception):
    pass


def run(*args, stdin=None):
    p = subprocess.run(args, input=stdin, capture_output=True, text=True)
    if p.returncode:
        detail = (p.stderr or p.stdout).strip().splitlines()
        raise SetupError(f"{Path(args[0]).name} failed: " + "\n".join(detail[-4:]))
    return p.stdout.strip()


def is_install_disk(device):
    # OpenZFS exposes guest volumes as ordinary TYPE=disk nodes (/dev/zdN).
    # Their filesystem signatures describe the guest, not the owning ZFS pool.
    return device["type"] == "disk" and not re.fullmatch(
        r"zd[0-9]+", Path(device["path"]).name
    )


def disks():
    data = json.loads(
        run(
            "lsblk",
            "--tree",
            "-Jbpo",
            "PATH,TYPE,SIZE,MODEL,SERIAL,RO,MOUNTPOINTS,FSTYPE,LABEL",
        )
    )

    def busy(node):
        # Pool members can be unmounted and have no block-device holders,
        # including when the pool is exported or this is a mirror member.
        if (
            node.get("label") == "YEETROOT"
            or any(node.get("mountpoints") or [])
            or node.get("fstype")
            in (
                "iso9660",
                "zfs_member",
            )
        ):
            return True
        if list(
            (Path("/sys/class/block") / Path(node["path"]).name / "holders").glob("*")
        ):
            return True
        return any(busy(child) for child in node.get("children", []))

    return [
        dev
        for dev in data["blockdevices"]
        if is_install_disk(dev)
        and not dev["ro"]
        and dev["size"] >= 10 * 1024**3
        and not busy(dev)
    ]


def validate(config):
    if not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", config.get("hostname", "")
    ):
        raise SetupError(
            "Choose a hostname of 1–63 lowercase letters, digits or hyphens."
        )
    if (
        config.get("version") != 1
        or not config.get("disk")
        or config.get("erase") != config["disk"]
    ):
        raise SetupError(
            "The version 1 install plan must explicitly confirm its destination disk."
        )
    dest = os.path.realpath(config["disk"])
    device = next((d for d in disks() if d["path"] == dest), None)
    if not device or (config.get("serial") and device["serial"] != config["serial"]):
        raise SetupError(
            "The selected disk is absent, in use, too small or has a different serial."
        )
    key = config.get("ssh_public_key", "")
    if key:
        if "\n" in key or not key.startswith(
            ("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-", "sk-")
        ):
            raise SetupError("Provide one OpenSSH public key.")
        run("ssh-keygen", "-l", "-f", "/dev/stdin", stdin=key + "\n")
    if not key and not config.get("password") and not config.get("password_hash"):
        raise SetupError("Set a console password or supply an SSH public key.")
    digest = config.get("password_hash", "")
    if digest and not re.fullmatch(r"\$(?:6|y)\$[./a-zA-Z0-9$=,]+", digest):
        raise SetupError("Use a SHA-512 or yescrypt password hash.")
    return device


@contextlib.contextmanager
def locked():
    with open("/run/yeet-setup.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SetupError("Another console is already installing Yeet OS.") from exc
        yield


def private_write(path, contents, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", opener=lambda p, flags: os.open(p, flags, mode)) as f:
        f.write(contents)
    path.chmod(mode)


def partition(disk, firmware):
    run("wipefs", "--all", disk)
    if firmware == "uefi":
        first = ["-n", "1:1MiB:+512MiB", "-t", "1:ef00", "-c", "1:YEETEFI"]
    else:
        first = ["-n", "1:1MiB:+2MiB", "-t", "1:ef02", "-c", "1:YEETBIOS"]
    run("sgdisk", "--zap-all", disk)
    run("sgdisk", *first, "-n", "2:0:0", "-t", "2:8300", "-c", "2:YEETROOT", disk)
    run("partprobe", disk)
    run("udevadm", "settle")
    nodes = json.loads(run("lsblk", "--tree", "-Jpo", "PATH,PARTN,TYPE", disk))[
        "blockdevices"
    ][0]["children"]
    parts = {int(d["partn"]): d["path"] for d in nodes if d["type"] == "part"}
    run("mkfs.ext4", "-q", "-F", "-L", "YEETROOT", parts[2])
    if firmware == "uefi":
        run("mkfs.vfat", "-F32", "-n", "YEETEFI", parts[1])
    run("udevadm", "settle")
    return parts


def configure_access(root, config):
    if config.get("ssh_public_key"):
        # First boot installs the key as the login user, even on a reused /home.
        private_write(root / "var/lib/yeet-os/installer-key", config["ssh_public_key"] + "\n")
    digest = config.get("password_hash")
    if config.get("password"):
        digest = run(
            "openssl", "passwd", "-6", "-stdin", stdin=config["password"] + "\n"
        )
    if digest:
        run("chpasswd", "-R", str(root), "-e", stdin="yeet:" + digest + "\n")


def validate_network(n):
    if not set(n) <= {
        "mode",
        "mac",
        "interface",
        "address",
        "gateway",
        "dns",
    }:
        raise ValueError("Unknown network setting in public profile.")
    if n["mode"] == "dhcp":
        return
    if n["mode"] != "static":
        raise ValueError("Choose DHCP or static IPv4.")
    if (n.get("mac") or n.get("interface")) and not (
        re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", n.get("mac", ""))
        or (
            not n.get("mac")
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", n.get("interface", ""))
        )
    ):
        raise ValueError(
            "Identify one network adapter by MAC address or interface name."
        )
    if "/" not in n["address"]:
        raise ValueError("Include the prefix length in the static address.")
    ipaddress.IPv4Interface(n["address"])
    ipaddress.IPv4Address(n["gateway"])
    if not n["dns"]:
        raise ValueError("Enter at least one DNS server.")
    for addr in n["dns"]:
        ipaddress.ip_address(addr)


def machine_profile(ui=None):
    path = Path("/etc/yeet-machine.json")
    if not path.exists():
        return None
    machine = json.loads(path.read_text())
    net = machine["network"]
    if net["mode"] != "static" or net.get("mac") or net.get("interface"):
        return machine
    nics = discovery_interfaces({})
    selected = Path("/run/yeet-selected-uplink")
    nic = selected.read_text().strip() if selected.is_file() else ""
    if nic not in nics:
        if len(nics) == 1:
            nic = nics[0]
        elif ui and nics:
            nic = nics[
                ui.choose(
                    "NETWORK",
                    "Connect the static address",
                    f"Choose the physical adapter for {net['address']}.",
                    [
                        f"{n}   {(Path('/sys/class/net') / n / 'address').read_text().strip()}"
                        for n in nics
                    ],
                )
            ]
        else:
            raise SetupError(
                "Choose a physical Ethernet adapter for the static address on the installer console."
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", nic):
            raise SetupError("Unsupported network interface name.")
        override = Path(
            "/run/systemd/network/10-yeet-uplink.network.d/50-selected.conf"
        )
        private_write(override, "[Match]\nName=\nName=" + nic + "\n", mode=0o644)
        run("networkctl", "reload")
        run("networkctl", "reconfigure", nic)
        private_write(selected, nic)
    net["mac"] = (Path("/sys/class/net") / nic / "address").read_text().strip()
    return machine


def discovery_interfaces(network, sysnet=Path("/sys/class/net")):
    interfaces = []
    for nic in sorted(sysnet.iterdir()):
        if not (nic / "device").exists() or (nic / "type").read_text().strip() != "1":
            continue
        if (
            network.get("mac")
            and (nic / "address").read_text().strip().lower() != network["mac"].lower()
        ):
            continue
        if network.get("interface") and nic.name != network["interface"]:
            continue
        interfaces.append(nic.name)
    return interfaces


def lan_uplink(interfaces, sysnet=Path("/sys/class/net")):
    wired = [nic for nic in interfaces if not (sysnet / nic / "wireless").exists()]
    if len(wired) == 1:
        return wired[0]
    if wired:
        routes = json.loads(run("ip", "-j", "-4", "route", "show", "default"))
        uplinks = {route.get("dev") for route in routes}
        if len(uplinks) == 1 and uplinks.issubset(wired):
            return uplinks.pop()
    return None


def prepare_system(site, config, firmware):
    machine = machine_profile()
    layout = config.get("layout")
    if not machine and config.get("network"):
        from storage import host_id

        net = config["network"]
        validate_network(net)
        machine = dict(
            hostname=config["hostname"],
            network=net,
            hostId=layout["hostId"] if layout else host_id(),
            sshPublicKey=config.get("ssh_public_key", ""),
        )
    if machine and config.get("recover_dataset"):
        machine = dict(machine, hostname=config["hostname"], hostId=layout["hostId"])
    site.mkdir(parents=True, exist_ok=True)
    (site / "firmware.json").write_text(json.dumps(firmware))
    (site / "machine.json").write_text(json.dumps(machine))
    lan = discovery_interfaces(machine["network"] if machine else {})
    host = {"hostname": config["hostname"], "interfaces": lan}
    if not config.get("recover_dataset") and not (layout or {}).get("bridges"):
        uplink = lan_uplink(lan)
        if uplink and layout is not None:
            # The existing recovery record preserves bridge-to-port mappings.
            layout["bridges"] = {"br0": uplink}
        elif uplink:
            host["lanUplink"] = uplink
    (site / "layout.json").write_text(json.dumps(layout))
    (site / "host.json").write_text(json.dumps(host))
    return build_system(
        run,
        os.environ["YEET_OS_SOURCE"],
        os.environ["YEET_NIXPKGS_SOURCE"],
        site,
        out_link=site / ".system",
    )


def enroll_attempt(state, hostname, key, event):
    argv = ["yeet-enroll", "--state", str(state), "--hostname", hostname]
    if key:
        argv += ["--key-file", str(key)]
    # Auth URLs only go to the console. A key stays in its private input file.
    with tempfile.TemporaryFile(mode="w+t") as errors:
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=errors) as child:
            try:
                with selectors.DefaultSelector() as poll:
                    poll.register(child.stdout, selectors.EVENT_READ)
                    pairing = False
                    while True:
                        if pairing:
                            alternate = event("poll", "")
                            if alternate:
                                return None, alternate
                        if not poll.select(0.25):
                            continue
                        line = child.stdout.readline()
                        if not line:
                            break
                        record = json.loads(line)
                        if record.get("ready"):
                            if child.wait() == 0:
                                return record["ready"], None
                            break
                        if record.get("url"):
                            event("pair", record["url"])
                            pairing = True
                errors.seek(0)
                raise SetupError(
                    errors.read().strip() or "Tailscale enrollment failed."
                )
            finally:
                if child.poll() is None:
                    child.terminate()


def enroll(root, hostname, key, event, data_dir=None):
    parent = data_dir if data_dir is not None else root / "var/lib/yeet"
    parent.mkdir(parents=True, exist_ok=True)
    state = parent / "tsnet"
    while True:
        # Stage enrollment beside the final directory so a cancelled attempt
        # cannot leave a half-registered identity for Catch to open on boot.
        with tempfile.TemporaryDirectory(prefix=".enroll-", dir=parent) as tmp:
            stage = Path(tmp, "tsnet")
            if state.exists():
                shutil.copytree(state, stage)
            name, alternate = enroll_attempt(stage, hostname, key, event)
            if name:
                private_write(
                    stage / "enrollment.env", "CATCH_TSNET_ADVERTISE_TAG=tag:catch\n"
                )
                if state.exists():
                    state.rename(Path(tmp, "previous"))
                try:
                    stage.rename(state)
                except OSError:
                    previous = Path(tmp, "previous")
                    if previous.exists():
                        previous.rename(state)
                    raise
                return name
            key = alternate
            event("progress", "Enrolling with the selected key file")


def install(config, key, event):
    # Build the selected hardware configuration before writing to the boot disk.
    from storage import (
        create_and_mount,
        recovery_descriptor,
        release_idle_pools,
        tpath,
        validate_layout,
    )

    device = validate(config)
    disk = device["path"]
    firmware = "uefi" if Path("/sys/firmware/efi").is_dir() else "bios"
    if Path("/dev/disk/by-label/YEETROOT").exists():
        raise SetupError(
            "Disconnect the previous Yeet OS boot disk before installing a replacement, or choose boot repair."
        )
    layout = config.get("layout")
    recovery = config.get("recover_dataset")
    legacy = config.get("legacy")
    if legacy and not recovery:
        raise SetupError("Choose the dataset that holds your original Yeet host data.")
    if recovery:
        if config.get("source_offline") is not True:
            raise SetupError(
                "Confirm that the original host is offline before restoring its identity."
            )
        if legacy:
            from legacy import adoption, stage_units

            saved, units, boot_units = adoption(
                recovery,
                config["hostname"],
                layout["hostId"],
                legacy["enabled"],
                legacy.get("engine"),
                legacy.get("bridges"),
            )
            if saved["unit_digest"] != legacy["unit_digest"]:
                raise SetupError(
                    "The saved unit files changed. Review the migration again."
                )
        else:
            saved = recovery_descriptor(recovery)
        if saved["hostname"] != config["hostname"] or saved["layout"] != layout:
            raise SetupError("The recovery record changed. Rescan storage.")
    elif layout:
        validate_layout(layout)
    from zfs_keys import verified_keys, install_keys

    keys = verified_keys(layout)
    if layout:
        layout = dict(layout)
        layout.pop("encryptionRoots", None)
        if keys:
            layout["encryptionRoots"] = sorted(keys)
        config = dict(config, layout=layout)
    with tempfile.TemporaryDirectory(prefix="yeet-site-") as stage:
        event("progress", "Preparing the NixOS configuration for this machine")
        profile = prepare_system(Path(stage), config, firmware)
        if not Path(profile, "bin/switch-to-configuration").is_file():
            raise SetupError("The installer could not prepare the host system.")
        if legacy:
            vendor = Path(profile, "etc/systemd/system")
            if not vendor.is_dir() or any(
                os.path.lexists(vendor / unit) for unit in units
            ):
                raise SetupError(
                    "A saved payload unit conflicts with the new host configuration."
                )
        device = validate(config)
        event("progress", "Partitioning " + disk)
        parts = partition(device["path"], firmware)
        Path("/mnt").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="yeet-root-", dir="/mnt") as tmp:
            root = Path(tmp)
            run("mount", parts[2], tmp)
            try:
                if firmware == "uefi":
                    (root / "boot").mkdir()
                    run("mount", "-o", "umask=0077", parts[1], str(root / "boot"))
                (root / "etc").mkdir(exist_ok=True)
                install_keys(root, keys)
                if layout:
                    create_and_mount(
                        layout, root, bool(recovery), bind_units=not legacy
                    )
                event("progress", "Installing the selected NixOS system")
                run(
                    "nixos-install",
                    "--root",
                    tmp,
                    "--system",
                    profile,
                    "--no-root-password",
                    "--no-channel-copy",
                )
                if firmware == "bios":
                    run(
                        "grub-install",
                        "--target=i386-pc",
                        "--boot-directory=" + str(root / "boot"),
                        disk,
                    )
                configure_access(root, config)
                (root / "etc/nixos").mkdir(parents=True, exist_ok=True)
                for name in FACTS:
                    shutil.copyfile(Path(stage, name), root / "etc/nixos" / name)
                data = tpath(
                    root, layout["data"]["path"] if layout else "/var/lib/yeet"
                )
                data.mkdir(parents=True, exist_ok=True)
                name = (layout or {}).get("catchHostname", config["hostname"])
                if not recovery:
                    ready = Path("/run/yeet-live/ready")
                    if ready.is_file():
                        shutil.copytree(ready.parent / "tsnet", data / "tsnet")
                        private_write(
                            data / "tsnet/enrollment.env",
                            "CATCH_TSNET_ADVERTISE_TAG=tag:catch\n",
                        )
                        name = ready.read_text().strip()
                    else:
                        event("progress", "Connecting your Yeet host to Tailscale")
                        name = enroll(
                            root, config["hostname"], key, event, data_dir=data
                        )
                if layout:
                    record = json.dumps(
                        {
                            "version": 1,
                            "hostname": config["hostname"],
                            "layout": layout,
                        },
                        indent=2,
                    )
                if legacy:
                    with stage_units(
                        data,
                        units,
                        boot_units,
                        legacy["enabled"],
                        vendor,
                        layout.get("networkUnits", []),
                    ) as staged:
                        target = data / "host-systemd"
                        if os.path.lexists(target) or os.path.lexists(
                            data / "yeet-os-layout.json"
                        ):
                            raise SetupError(
                                "The destination recovery state changed. Rescan storage."
                            )
                        private_write(
                            Path(staged, "host-systemd/yeet-os-layout.json"), record
                        )
                        Path(staged, "host-systemd").rename(target)
                elif layout and not recovery:
                    private_write(data / "yeet-os-layout.json", record)
                run("sync", "-f", tmp)
            finally:
                run("umount", "-R", tmp)
            if layout:
                release_idle_pools(layout)
            return name


def existing_host(event):
    name = socket.gethostname()
    if subprocess.run(["systemctl", "is-active", "--quiet", "catch"]).returncode:
        run("systemctl", "stop", "catch")
        layout_file = Path("/etc/nixos/layout.json")
        layout = json.loads(layout_file.read_text()) if layout_file.exists() else None
        data = Path(layout["data"]["path"]) if layout else None
        name = enroll(Path("/"), name, None, event, data_dir=data)
        run("systemctl", "start", "catch")
    run("catch", "host-check")
    return name
