"""Recover ZFS wrapping keys without putting them into a Nix build input."""

import hashlib
import os
from pathlib import Path
import re
import tempfile

from provision import SetupError, run

LIVE = Path("/run/yeet-zfs-keys")
INSTALLED = "etc/yeet-zfs-keys"
MAX_KEY_BYTES = 4096


def inventory():
    rows = run("zfs", "list", "-H", "-t", "filesystem,volume", "-o",
               "name,encryptionroot,keystatus,canmount")
    return {name: (root, status, mountable)
            for name, root, status, mountable in (line.split("\t") for line in rows.splitlines())}


def filename(root):
    return hashlib.sha256(root.encode()).hexdigest() + ".key"


def upload(root, source):
    rows = inventory()
    if root not in rows or rows[root][0] != root or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*", root):
        raise SetupError("Import the pool and choose an existing ZFS encryption root.")
    data = source.read(MAX_KEY_BYTES + 1)
    if not 0 < len(data) <= MAX_KEY_BYTES:
        raise SetupError("Supply a nonempty ZFS key file of up to 4096 bytes.")
    LIVE.mkdir(mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=LIVE, delete=False) as temp:
        path = Path(temp.name)
        try:
            temp.write(data)
            temp.flush()
            uri = path.as_uri()
            run("zfs", "load-key", "-n", "-L", uri, root)
            if rows[root][1] != "available":
                run("zfs", "load-key", "-L", uri, root)
            os.replace(path, LIVE / filename(root))
        finally:
            path.unlink(missing_ok=True)


def required_roots(layout, rows):
    selected = set(layout.get("vmVolumes", []))
    for loc in (layout["data"], layout["services"], *layout.get("extraFilesystems", [])):
        if ds := loc.get("dataset"):
            # A fresh child inherits its parent's encryption root.
            selected.add(ds.rpartition("/")[0] if ds not in rows and loc.get("create") else ds)
    services = layout["services"].get("dataset")
    if services:
        selected.update(ds for ds, (_, _, canmount) in rows.items()
                        if ds.startswith(services + "/") and canmount == "on")
    missing = selected - rows.keys()
    if missing:
        raise SetupError(f"Import the original pool for {sorted(missing)[0]} and rescan.")
    return sorted({rows[ds][0] for ds in selected if rows[ds][0] != "-"})


def verified_keys(layout):
    if not layout:
        return {}
    locations = [layout["data"], layout["services"], *layout.get("extraFilesystems", [])]
    if not any(loc.get("dataset") for loc in locations) and not layout.get("vmVolumes"):
        return {}
    rows = inventory()
    keys = {}
    for root in required_roots(layout, rows):
        path = LIVE / filename(root)
        if not path.is_file() or rows[root][1] != "available":
            raise SetupError(f"Supply the key for {root} over SSH before installing. Open Encrypted ZFS recovery for the command.")
        run("zfs", "load-key", "-n", "-L", path.as_uri(), root)
        keys[root] = path.read_bytes()
    return keys


def install_keys(target, keys):
    if not keys:
        return
    directory = target / INSTALLED
    directory.mkdir(mode=0o700)
    for root, value in keys.items():
        with open(os.open(directory / filename(root), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o400), "wb") as key:
            key.write(value)
