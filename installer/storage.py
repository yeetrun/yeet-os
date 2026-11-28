"""Physical storage discovery and per-host NixOS layout for the installer."""

import contextlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
import tempfile

from provision import SetupError, is_install_disk, run


def tpath(root, absolute):
    path = root / absolute.removeprefix("/")
    if path.resolve() != path:
        raise SetupError(
            f"Unexpected symbolic link at {absolute} in the target filesystem."
        )
    return path


def data_path(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
        or str(PurePosixPath(value)) != value
        or ".." in Path(value).parts
    ):
        raise SetupError("Enter an absolute directory without spaces or .. components.")
    if (
        value.split("/")[1]
        in (
            "",
            "dev",
            "root",
            "lib64",
            "proc",
            "sys",
            "etc",
            "run",
            "nix",
            "boot",
            "bin",
            "usr",
            "lib",
            "sbin",
            "tmp",
        )
        or value in ("/var", "/var/lib", "/home", "/srv", "/mnt")
        or value.startswith("/var/lib/yeet-systemd")
        or value.startswith("/var/lib/yeet-host")
    ):
        raise SetupError("Choose a dedicated data directory.")
    return value


def datasets(*, include_locked=False):
    raw = run(
        "zfs",
        "list",
        "-H",
        "-t",
        "filesystem",
        "-o",
        "name,mountpoint,available,keystatus,canmount",
        "-p",
    )
    result = {}
    for line in raw.splitlines():
        name, point, available, keys, mountable = line.split("\t")
        if (keys == "unavailable" and not include_locked) or mountable == "off" or not point.startswith("/"):
            continue
        result[name] = (point, 0 if available == "-" else int(available))
    return result


def importable():
    # Listing pools does not import or mount any of their filesystems.
    result = subprocess.run(["zpool", "import"], capture_output=True, text=True)
    if result.returncode:
        raise SetupError(result.stderr.strip() or "Unable to discover pools.")
    return re.findall(r"^\s*pool:\s*(\S+)\s*\n\s*id:\s*(\d+)\s*$", result.stdout, re.M)


def location(dataset, path, create=False):
    loc = {"path": data_path(path)}
    if dataset:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*(?:/[A-Za-z0-9_.:-]+)*", dataset):
            raise SetupError("Select a ZFS filesystem with a simple dataset name.")
        loc["dataset"] = dataset
        if create:
            loc["create"] = True
    return loc


def mount_dataset(dataset, target, readonly=False):
    mounted = subprocess.run(
        ["findmnt", "-J", "-l", "-S", dataset, "-o", "TARGET,FSROOT"],
        capture_output=True,
        text=True,
    )
    sources = (
        json.loads(mounted.stdout).get("filesystems", [])
        if mounted.returncode == 0
        else []
    )
    source = next((m["target"] for m in sources if m["fsroot"] == "/"), None)
    if source:
        run("mount", "--bind", source, str(target))
        if readonly:
            try:
                run("mount", "-o", "remount,bind,ro,nodev,nosuid,noexec", str(target))
            except SetupError:
                run("umount", str(target))
                raise
    else:
        run(
            "mount",
            "-t",
            "zfs",
            "-o",
            "ro,zfsutil,nodev,nosuid,noexec" if readonly else "zfsutil",
            dataset,
            str(target),
        )


@contextlib.contextmanager
def view(dataset):
    with tempfile.TemporaryDirectory(prefix="yeet-read-") as d:
        mount_dataset(dataset, d, readonly=True)
        try:
            yield Path(d)
        finally:
            run("umount", d)


def extra_locations(layout):
    extras = layout.get("extraFilesystems", [])
    if not extras:
        return []
    seen = {s["path"] for s in (layout["data"], layout["services"])}
    names = {s.get("dataset") for s in (layout["data"], layout["services"])}
    for loc in extras:
        if set(loc) != {"dataset", "path"} or not loc["dataset"]:
            raise SetupError(
                "An additional filesystem needs a dataset and a mountpoint."
            )
        location(loc["dataset"], loc["path"])
        if loc["path"] in seen or loc["dataset"] in names:
            raise SetupError(
                "Two storage locations refer to the same dataset or mountpoint."
            )
        names.add(loc["dataset"])
        seen.add(loc["path"])
    return extras


def validate_layout(layout, recover=False):
    data, services = layout["data"], layout["services"]
    for s in (data, services):
        location(s.get("dataset"), s["path"], s.get("create", False))
    a, b = map(lambda s: PurePosixPath(s["path"]), (data, services))
    if a == b or b in a.parents or (a in b.parents and b != a / "services"):
        raise SetupError(
            "Use separate storage roots, or the services directory directly inside the Yeet host data directory."
        )
    if not re.fullmatch("[0-9a-f]{8}", layout["hostId"]):
        raise SetupError("Invalid ZFS host ID.")
    locations = [data, services, *extra_locations(layout)]
    if not any("dataset" in s for s in locations):
        return
    inventory = datasets()
    engines = layout.get("engineStorage", {})
    if engines:
        if set(engines) != {"docker", "containerd"}:
            raise SetupError("The saved layout needs both original container runtimes.")
        for role, loc in engines.items():
            if not isinstance(loc, dict) or set(loc) != {"dataset", "path"}:
                raise SetupError(f"Invalid {role} storage entry.")
            location(loc["dataset"], loc["path"])
            if (
                inventory.get(loc["dataset"], (None,))[0] != loc["path"]
                or loc not in locations
            ):
                raise SetupError(f"The original {role} dataset is missing or moved.")
    all_names = set(run("zfs", "list", "-H", "-o", "name").splitlines())
    for volume in layout.get("vmVolumes", []):
        location(volume, "/srv/volume")
        if volume not in all_names or not Path("/dev/zvol", volume).is_block_device():
            raise SetupError(f"Restore the original VM disk {volume}.")
    for s in locations:
        ds = s.get("dataset")
        if not ds:
            continue
        if ds in inventory:
            if inventory[ds][0] != s["path"]:
                raise SetupError(f"The mountpoint for {ds} changed. Rescan storage.")
            if recover:
                continue
            if s.get("create"):
                raise SetupError(f"{ds} already exists. Pick a new name.")
            if any(n.startswith(ds + "/") for n in all_names):
                raise SetupError(
                    f"{ds} has child datasets. Pick a new child for a fresh install."
                )
            with view(ds) as r:
                if any(p.name != ".zfs" for p in r.iterdir()):
                    raise SetupError(
                        f"{ds} contains data. Pick a new child or select recovery."
                    )
        elif not recover and s.get("create") and ds not in all_names:
            parent, _, child = ds.rpartition("/")
            if (
                parent not in inventory
                or str(Path(inventory[parent][0]) / child) != s["path"]
            ):
                raise SetupError("The selected parent dataset or mountpoint changed.")
        else:
            raise SetupError(f"{ds} is absent, locked or unavailable.")


def create_and_mount(layout, root, recover=False, bind_units=True):
    locations = [layout["data"], layout["services"], *extra_locations(layout)]
    for loc in sorted(locations, key=lambda loc: len(Path(loc["path"]).parts)):
        dst = tpath(root, loc["path"])
        dst.mkdir(parents=True, exist_ok=True)
        if ds := loc.get("dataset"):
            if loc.get("create") and not recover:
                run("zfs", "create", "-u", ds)
            mount_dataset(ds, dst)
    if ds := layout["services"].get("dataset"):
        if not recover:
            run("zfs", "create", "-u", ds + "/catch")
        base = Path(layout["services"]["path"])
        for child, (mountpoint, _) in sorted(
            datasets().items(), key=lambda item: item[0].count("/")
        ):
            if not child.startswith(ds + "/"):
                continue
            if not Path(mountpoint).is_relative_to(base):
                raise SetupError(f"Unexpected mountpoint {mountpoint} for {child}.")
            dst = tpath(root, mountpoint)
            dst.mkdir(parents=True, exist_ok=True)
            mount_dataset(child, dst)
    if not bind_units:
        return
    data = tpath(root, layout["data"]["path"])
    (data / "host-systemd/etc/systemd/system").mkdir(parents=True, exist_ok=True)
    units = root / "var/lib/yeet-systemd"
    units.mkdir(parents=True, exist_ok=True)
    run("mount", "--bind", str(data / "host-systemd"), str(units))


def release_idle_pools(layout):
    # A replacement may use the saved host ID rather than the live ISO's ID.
    # Export only selected pools with no remaining live filesystem mounts.
    pools = {
        loc["dataset"].split("/")[0]
        for loc in (
            layout["data"],
            layout["services"],
            *layout.get("extraFilesystems", []),
        )
        if loc.get("dataset")
    }
    pools.update(vol.split("/")[0] for vol in layout.get("vmVolumes", []))
    for pool in sorted(pools):
        mounted = run(
            "zfs", "list", "-H", "-r", "-t", "filesystem", "-o", "mounted", pool
        )
        if "yes" not in mounted.splitlines():
            run("zpool", "export", pool)


def check_saved_services(root, layout):
    db = json.loads((root / "db.json").read_text())
    services = db.get("Services") or {}
    if not isinstance(services, dict):
        raise SetupError("Yeet couldn’t read the saved app list.")
    base = Path(layout["services"]["path"])
    inventory = datasets()
    by_path = {Path(point): ds for ds, (point, _) in inventory.items()}
    for name, service in services.items():
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", name) or name in (".", ".."):
            raise SetupError("A saved service name is invalid.")
        path = Path(service.get("ServiceRoot") or base / name)
        extras = {s["dataset"]: s["path"] for s in extra_locations(layout)}
        if ".." in path.parts or (
            not path.is_relative_to(base) and str(path) not in extras.values()
        ):
            raise SetupError(
                f"{name} uses an external root at {path}. Restore its original filesystems before recovering this host."
            )
        ds = service.get("ServiceRootZFS") or by_path.get(path)
        if (
            ds
            and ds not in extras
            and not ds.startswith(layout["services"].get("dataset", "") + "/")
        ):
            raise SetupError(
                f"{name} depends on a separate dataset outside the selected services tree."
            )
        if ds and (ds not in inventory or inventory[ds][0] != str(path)):
            raise SetupError(f"The original dataset for {name} is missing or moved.")
        if not ds and "dataset" in layout["services"]:
            with view(layout["services"]["dataset"]) as parent:
                if not (parent / path.relative_to(base)).is_dir():
                    raise SetupError(f"The saved root for {name} is missing.")
        if not ds and "dataset" not in layout["services"]:
            original = root / path.relative_to(layout["data"]["path"])
            if not original.is_dir():
                raise SetupError(f"The saved root for {name} is missing.")
        if (
            service.get("ServiceType") == "docker-compose"
            and not layout.get("engineStorage")
            and not (root / "host-runtime/docker").is_dir()
        ):
            raise SetupError(
                "The original Docker runtime storage is missing. Use the original OS disk or restore its backup."
            )
    return len(services)


class NoSavedSettings(SetupError):
    """The selected filesystem has no Yeet OS recovery record."""


def recovery_descriptor(dataset):
    with view(dataset) as r:
        # The manifest records paths and pool names, never credentials.
        file = r / "yeet-os-layout.json"
        if not os.path.lexists(file):
            file = r / "host-systemd/yeet-os-layout.json"
        if not os.path.lexists(file):
            raise NoSavedSettings(f"No saved Yeet OS settings were found on {dataset}.")
        if file.resolve() != file or not file.is_file():
            raise SetupError(
                f"The saved Yeet OS settings on {dataset} must be a regular file."
            )
        try:
            manifest = json.loads(file.read_text())
        except ValueError as exc:
            raise SetupError(
                f"Yeet couldn’t read {file.name} on {dataset}. Restore the saved settings file from backup."
            ) from exc
        if (
            manifest.get("version") != 1
            or manifest["layout"]["data"].get("dataset") != dataset
        ):
            raise SetupError(
                "This recovery record does not match the selected data dataset."
            )
        for path in (
            "db.json",
            "tsnet/tailscaled.state",
            "host-systemd/etc/systemd/system",
        ):
            if not (r / path).exists():
                raise SetupError(f"Recovery needs {path} in {dataset}.")
        if not manifest["layout"]["services"].get("dataset") and not PurePosixPath(
            manifest["layout"]["services"]["path"]
        ).is_relative_to(PurePosixPath(manifest["layout"]["data"]["path"])):
            raise SetupError(
                "This host used service storage on another filesystem. Recover with its original OS disk attached."
            )
        validate_layout(manifest["layout"], recover=True)
        manifest["service_count"] = check_saved_services(r, manifest["layout"])
        return manifest


def host_id():
    return secrets.token_hex(4)


def os_disks():
    """Find Yeet root partitions without writing to or replaying their journals."""
    raw = json.loads(
        run(
            "lsblk",
            "--tree",
            "-Jbpo",
            "PATH,TYPE,SIZE,MODEL,SERIAL,RO,LABEL,FSTYPE,PARTN,MOUNTPOINTS",
        )
    )
    hosts = []
    for disk in raw["blockdevices"]:
        if not is_install_disk(disk) or disk.get("ro"):
            continue
        parts = disk.get("children", [])
        if any(any(p.get("mountpoints") or []) for p in parts):
            continue
        roots = [
            p
            for p in parts
            if p.get("label") == "YEETROOT" and p.get("fstype") == "ext4"
        ]
        if len(roots) != 1:
            continue
        with tempfile.TemporaryDirectory(prefix="yeet-inspect-") as tmp:
            root = Path(tmp)
            run("mount", "-o", "ro,noload,nodev,nosuid,noexec", roots[0]["path"], tmp)
            try:
                # /etc/yeet-os/managed is normally a link into the installed store.
                if (
                    not os.path.lexists(root / "etc/yeet-os/managed")
                    or not (root / "nix/var/nix/profiles/system").is_symlink()
                ):
                    continue
                # New installations declare the hostname in the site flake.
                # Its activated /etc/hostname can point into the target store.
                site_host = root / "etc/nixos/host.json"
                name = (
                    json.loads(site_host.read_text())["hostname"]
                    if site_host.is_file()
                    else (root / "etc/hostname").read_text().strip()
                )
                esp = next(
                    (
                        p["path"]
                        for p in parts
                        if p.get("label") == "YEETEFI" and p.get("fstype") == "vfat"
                    ),
                    None,
                )
                hosts.append(
                    dict(
                        disk=disk["path"],
                        root=roots[0]["path"],
                        esp=esp,
                        hostname=name,
                        serial=disk["serial"],
                    )
                )
            finally:
                run("umount", tmp)
    return hosts


def repair_os(selected):
    # Retain the existing system generation, service units, datasets, SSH keys
    # and Catch identity. Only reinstall its bootloader on its original disk.
    if selected not in os_disks():
        raise SetupError("The selected installation changed. Rescan the disks.")
    if bool(selected["esp"]) != Path("/sys/firmware/efi").is_dir():
        raise SetupError(
            "Reboot this installer in the same firmware mode as the original system."
        )
    with tempfile.TemporaryDirectory(prefix="yeet-repair-") as tmp:
        run("mount", selected["root"], tmp)
        try:
            if selected["esp"]:
                run("mount", selected["esp"], str(Path(tmp, "boot")))
            run(
                "nixos-enter",
                "--root",
                tmp,
                "--",
                "/nix/var/nix/profiles/system/sw/bin/env",
                "NIXOS_INSTALL_BOOTLOADER=1",
                "/nix/var/nix/profiles/system/bin/switch-to-configuration",
                "boot",
            )
            if not selected["esp"]:
                run(
                    "grub-install",
                    "--target=i386-pc",
                    "--boot-directory=" + str(Path(tmp, "boot")),
                    selected["disk"],
                )
        finally:
            run("umount", "-R", tmp)
