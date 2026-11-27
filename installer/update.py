"""Update Yeet OS and manage the active and next-boot generations."""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from system_build import build_system

RELEASES = "https://github.com/yeetrun/yeet-os/releases"
SYSTEM = platform.machine() + "-linux"
PROFILE = Path("/nix/var/nix/profiles/system")
RUNNING = Path("/run/current-system")
BOOTED = Path("/run/booted-system")
SITE = Path("/etc/nixos")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
STORE = re.compile(r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[\w.+?=-]+")
PARTS = ("source", "nixpkgs", "runtime")


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def version(profile):
    file = profile / "etc/yeet-os/version"
    return file.read_text().strip() if file.is_file() else "legacy"


def asset(part, system):
    return f"yeet-os-{part}-{system}.nix.zst"


def validate(data, system):
    if (
        not isinstance(data, dict)
        or data.get("format") != 1
        or data.get("system") != system
    ):
        raise ValueError("Unsupported release format or architecture")
    if not VERSION.fullmatch(str(data.get("version", ""))):
        raise ValueError("Invalid release version")
    for part in PARTS:
        item = data[part]
        if not STORE.fullmatch(item["path"]) or not re.fullmatch(
            "[a-f0-9]{64}", item["sha256"]
        ):
            raise ValueError(f"Invalid {part} in the release manifest")
    return data


def fetch(url, dest):
    with urllib.request.urlopen(url, timeout=90) as response, dest.open("wb") as file:
        shutil.copyfileobj(response, file)


def metadata(local, work):
    if local:
        manifest = local.resolve()
    else:
        manifest = work / "manifest.json"
        fetch(f"{RELEASES}/latest/download/yeet-os-{SYSTEM}.json", manifest)
    return validate(json.loads(manifest.read_bytes()), SYSTEM)


def restore_parts(data, local, work):
    paths = {}
    for part in PARTS:
        item = data[part]
        path = Path(item["path"])
        if subprocess.run(
            ["nix-store", "--check-validity", path],
            check=False,
            stderr=subprocess.DEVNULL,
        ).returncode:
            name = asset(part, SYSTEM)
            packed = local.resolve().parent / name if local else work / name
            if not local:
                fetch(f"{RELEASES}/download/v{data['version']}/{name}", packed)
            with packed.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError(f"Checksum mismatch for {name}")
            with subprocess.Popen(
                ["zstd", "-dc", packed], stdout=subprocess.PIPE
            ) as decoder:
                subprocess.run(
                    ["nix-store", "--import"],
                    stdin=decoder.stdout,
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
                if decoder.wait() != 0:
                    raise ValueError(f"Could not decompress {name}")
            if not local:
                packed.unlink()
        run("nix-store", "--realise", str(path), "--add-root", str(work / part))
        paths[part] = path
    if (paths["source"] / "VERSION").read_text().strip() != data["version"]:
        raise ValueError("The release source and manifest versions differ")
    return paths


def machine_site(work):
    if (RUNNING / "etc/yeet-os/image").is_file():
        return None
    site = work / "site"
    site.mkdir()
    for name in ("host.json", "machine.json", "layout.json"):
        if not (SITE / name).is_file():
            raise ValueError(f"Missing installer settings in {SITE / name}")
        shutil.copyfile(SITE / name, site / name)
    if (SITE / "firmware.json").is_file():
        firmware = json.loads((SITE / "firmware.json").read_text())
    else:
        # v0.2 installers stored the selected bootloader in their generated flake.
        old = (SITE / "flake.nix").read_text()
        match = re.search(r'base = os\.lib\.installed machine "(uefi|bios)";', old)
        if not match:
            raise ValueError(
                "Cannot identify the bootloader in the old installer configuration"
            )
        firmware = match[1]
    if firmware not in ("uefi", "bios"):
        raise ValueError("Unrecognized installer firmware setting")
    (site / "firmware.json").write_text(json.dumps(firmware))
    return site


def select_generation(action, *args):
    previous = PROFILE.readlink()
    active = RUNNING.resolve(strict=True)
    try:
        subprocess.run(["nixos-rebuild", action, "--no-reexec", *args], check=True)
    except (KeyboardInterrupt, subprocess.CalledProcessError):
        if PROFILE.readlink() != previous:
            number = re.fullmatch(r"system-(\d+)-link", previous.name)[1]
            subprocess.run(
                ["nix-env", "-p", PROFILE, "--switch-generation", number], check=True
            )
            subprocess.run(
                [PROFILE / "bin/switch-to-configuration", "boot"], check=True
            )
        # A switch may fail after changing units, even before current-system moves.
        if action == "switch":
            subprocess.run(
                ["nixos-rebuild", "test", "--no-reexec", "--store-path", active],
                check=True,
            )
        raise


def changed_inhibitors(target):
    def inhibitors(profile):
        file = profile / "switch-inhibitors"
        return json.loads(file.read_bytes()) if file.is_file() else {}

    old, new = inhibitors(RUNNING), inhibitors(target)
    # Match NixOS's pre-switch check: a shared key whose value changed.
    return sorted(key for key in old.keys() & new.keys() if old[key] != new[key])


def boot_changes(target):
    if not BOOTED.is_dir():
        return []
    changed = []
    for name in ("kernel", "kernel-modules", "initrd"):
        old, new = BOOTED / name, target / name
        if (old.exists() or new.exists()) and old.resolve() != new.resolve():
            changed.append(name)

    def kernel_params(profile):
        path = profile / "kernel-params"
        return path.read_bytes() if path.is_file() else None

    if kernel_params(BOOTED) != kernel_params(target):
        changed.append("kernel parameters")
    return changed


def status():
    print(f"Running: {version(RUNNING)}\nNext boot: {version(PROFILE)}")
    active = RUNNING.resolve() == PROFILE.resolve()
    if not active:
        print("A different system generation is selected for the next boot.")
    changed = boot_changes(PROFILE)
    if changed:
        print("Reboot to apply changes to " + ", ".join(changed) + ".")
    elif active and BOOTED.is_dir():
        print("No reboot needed for this generation.")


def build_generation(command, manifest, work, boot_only=False):
    if command == "rebuild":
        source = PROFILE / "yeet-os-source"
        if not source.exists():
            raise ValueError(
                "Upgrade this legacy generation first to pin its release sources."
            )
        nixpkgs = (PROFILE / "yeet-os-nixpkgs").resolve(strict=True)
        source = source.resolve()
    else:
        data = metadata(manifest, work)
        available = data["version"]
        print(f"Available: {available}", flush=True)
        pending = version(PROFILE)
        if VERSION.fullmatch(pending) and version_tuple(pending) > version_tuple(
            available
        ):
            raise ValueError(
                "This release is older than the selected generation. Use yeet-os rollback."
            )
        parts = restore_parts(data, manifest, work)
        source, nixpkgs = parts["source"], parts["nixpkgs"]
    site = machine_site(work)
    system = build_system(
        run, source, nixpkgs, site, SYSTEM, work / "system", local=SITE / "local"
    )
    target = Path(system)
    blockers = [] if boot_only else changed_inhibitors(target)
    if blockers:
        print("NixOS requires a reboot for changes to " + ", ".join(blockers) + ".", flush=True)
    action = "boot" if boot_only or blockers else "switch"
    if target != PROFILE.resolve() or (action == "switch" and target != RUNNING.resolve()):
        select_generation(action, "--store-path", system)
    if site is not None and not (SITE / "firmware.json").exists():
        # Keep the old site flake for rollback. Future updates only need the facts.
        with tempfile.NamedTemporaryFile(dir=SITE, delete=False) as firmware:
            firmware.write((site / "firmware.json").read_bytes())
        os.replace(firmware.name, SITE / "firmware.json")
    status()


def version_tuple(value):
    return tuple(map(int, value.split(".")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["status", "check", "upgrade", "rebuild", "rollback"]
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="use a local release manifest and its sibling assets",
    )
    parser.add_argument(
        "--boot-only", action="store_true",
        help="stage an upgrade or rebuild for the next boot without activating it now",
    )
    args = parser.parse_args()
    if args.manifest and args.command not in ("upgrade", "check"):
        parser.error("--manifest applies to check and upgrade")
    if args.boot_only and args.command not in ("upgrade", "rebuild"):
        parser.error("--boot-only applies to upgrade and rebuild")
    if not (RUNNING / "etc/yeet-os/managed").is_file():
        parser.exit(1, "Run this command on an installed Yeet OS host.\n")
    if args.command == "status":
        status()
        return
    with tempfile.TemporaryDirectory(prefix="yeet-update-") as tmp:
        work = Path(tmp)
        if args.command == "check":
            data = metadata(args.manifest, work)
            status()
            print(f"Available: {data['version']}")
            return
        if os.getuid() != 0:
            parser.exit(
                1, "Sign in as root or use sudo to change the boot generation.\n"
            )
        with open("/run/yeet-os-update.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if args.command == "rollback":
                select_generation("boot", "--rollback")
                print(
                    f"Selected {version(PROFILE)} for the next boot. Reboot when ready."
                )
            else:
                build_generation(args.command, args.manifest, work, args.boot_only)


if __name__ == "__main__":
    try:
        main()
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        sys.exit(f"yeet-os: {error}")
