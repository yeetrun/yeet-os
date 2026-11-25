"""Inspect an offline Catch deployment using the storage visible to the installer."""

from contextlib import ExitStack
from pathlib import Path
import os
import tempfile
import hashlib
import json
import shlex
import re

from provision import SetupError, run, discovery_interfaces
from legacy_units import payload_units, network_units
from storage import data_path, datasets, location, validate_layout, view


def regular(root, path):
    """Never follow a link from an offline filesystem into the installer."""
    for parent in (path, *path.parents):
        if parent == root:
            return path.is_file()
        if parent.is_symlink():
            return False
    return False


def inventory(dataset, *, read_units=False):
    filesystems = datasets()
    volumes = set(run("zfs", "list", "-H", "-t", "volume", "-o", "name").splitlines())
    with ExitStack() as stack:
        views = {}

        def mounted(ds):
            if ds not in views:
                views[ds] = stack.enter_context(view(ds))
            return views[ds]

        def backing(path):
            matches = [
                (len(Path(mp).parts), ds)
                for ds, (mp, _) in filesystems.items()
                if path.is_relative_to(mp)
            ]
            if not matches:
                return []
            depth = max(n for n, _ in matches)
            return [ds for n, ds in matches if n == depth]

        def locate(path, origins):
            if not path.is_absolute() or ".." in path.parts:
                return None
            owners = backing(path)
            if len(owners) != 1:
                return None
            ds = owners[0]
            origins.add(ds)
            source = mounted(ds)
            candidate = source / path.relative_to(filesystems[ds][0])
            return candidate if regular(source, candidate) else None

        base = mounted(dataset)
        db = base / "db.json"
        if not regular(base, db):
            raise SetupError(
                f"Yeet couldn’t find its host data on {dataset}. Choose the dataset with db.json at its top level."
            )
        records = json.loads(db.read_bytes()).get("Services")
        if not isinstance(records, dict):
            raise SetupError("Yeet couldn’t find the saved app list in db.json.")
        data = Path(filesystems[dataset][0])
        default = (records.get("catch") or {}).get("ServiceRoot")
        services = Path(default).parent if default else data / "services"
        report = []
        for name, svc in sorted(records.items()):
            if (
                not isinstance(svc, dict)
                or svc.get("Name", name) != name
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
            ):
                raise SetupError("An entry in the saved Yeet app list is invalid.")
            path = Path(data_path(svc.get("ServiceRoot") or str(services / name)))
            declared = svc.get("ServiceRootZFS")
            owners = backing(path)
            origins = set()
            owner = declared or (owners[0] if len(owners) == 1 else None)
            entry = dict(
                name=name,
                kind=svc.get("ServiceType") or "unknown",
                root=str(path),
                dataset=owner,
                problems=[],
                units={},
            )
            report.append(entry)
            if owner not in filesystems or owner not in owners:
                entry["problems"].append(
                    "Import the filesystem for the recorded service root."
                )
                continue
            mountpoint = Path(filesystems[owner][0])
            src = mounted(owner)
            relative = path.relative_to(mountpoint)
            directory = src / relative
            if not directory.is_dir() or directory.resolve() != directory:
                entry["problems"].append(
                    "The original service directory is missing or is a symbolic link."
                )
                continue
            generation = f"gen-{svc.get('Generation', 0)}"
            for key, artifact in (svc.get("Artifacts") or {}).items():
                ref = (artifact.get("Refs") or {}).get(generation)
                if not ref:
                    continue
                artifact_path = Path(ref)
                found = locate(artifact_path, origins)
                if found is None:
                    entry["problems"].append(
                        f"Restore the active {key} artifact at {artifact_path}."
                    )
                elif key.endswith((".service", ".timer")):
                    if not artifact_path.is_relative_to(path):
                        entry["problems"].append(
                            f"Review external unit file for {key}."
                        )
                        continue
                    entry["units"][key] = str(artifact_path)
                    if read_units and key in (
                        "systemd.service",
                        "systemd.timer",
                        "netns.service",
                        "tailscale.service",
                    ):
                        unit = found
                        if unit.stat().st_size > 1024 * 1024:
                            raise SetupError(f"{name} has an oversized unit file.")
                        entry.setdefault("contents", {})[key] = unit.read_text()
            vm = svc.get("VM") or {}
            disk = (vm.get("Disk") or {}).get("Path")
            if disk:
                entry["disk"] = disk
                if (
                    disk.startswith("/dev/zvol/")
                    and disk.removeprefix("/dev/zvol/") not in volumes
                ):
                    entry["problems"].append(
                        "Import the pool containing the VM disk volume."
                    )
            parents = {n.get("Parent") for n in vm.get("Networks") or []}
            parents.add((svc.get("Macvlan") or {}).get("Parent"))
            entry["parents"] = sorted(p for p in parents if p)
            entry["identity"] = svc.get("Identity") or {}
            entry["sandbox"] = ((svc.get("Sandbox") or {}).get("Refs") or {}).get(
                generation
            ) or {}
            if read_units and svc.get("ServiceType") == "vm":
                entry["vmRecord"] = svc
                components = vm.get("Components") or {}
                runtime = (components.get("Runtime") or {}).get("Configured") or {}
                inputs = [path / "run/firecracker.json", path / "data/vmm-runtime.json"]
                inputs.extend(
                    Path(p)
                    for p in (
                        runtime.get("Firecracker"),
                        runtime.get("Jailer"),
                        (vm.get("Image") or {}).get("Kernel"),
                        (vm.get("Image") or {}).get("RootFS"),
                    )
                    if p
                )
                if disk and not disk.startswith("/dev/zvol/"):
                    inputs.append(Path(disk))
                for file in inputs:
                    if locate(file, origins) is None:
                        entry["problems"].append(f"Restore the VM file {file}.")
            entry["filesystems"] = sorted(origins)
            if name == "catch" and read_units:
                entry["catchName"] = ""
                for line in (
                    entry.get("contents", {}).get("systemd.service", "").splitlines()
                ):
                    if line.startswith("ExecStart="):
                        args = shlex.split(line.removeprefix("ExecStart="))
                        for n, arg in enumerate(args):
                            if arg.lstrip("-").startswith("tsnet-host="):
                                entry["catchName"] = arg.split("=", 1)[1]
                            elif arg.lstrip("-") == "tsnet-host" and n + 1 < len(args):
                                entry["catchName"] = args[n + 1]
        return dict(
            data=str(data),
            dataset=dataset,
            services=str(services),
            has_record=os.path.lexists(base / "yeet-os-layout.json"),
            has_units=os.path.lexists(base / "host-systemd"),
            identity=regular(base, base / "tsnet/tailscaled.state"),
            workloads=report,
        )


def adoption(dataset, hostname, host_id, enabled, engine=None, bridges=None):
    """Build the proposed host from active generations. Never write during discovery."""
    report = inventory(dataset, read_units=True)
    if report["has_record"]:
        raise SetupError(
            "Saved Yeet OS settings were found. Return to recovery and select this dataset again."
        )
    if report["has_units"]:
        raise SetupError(
            "This dataset contains saved startup files. Restore its matching Yeet OS settings file (yeet-os-layout.json) before continuing."
        )
    if not report["identity"]:
        raise SetupError(
            "Restore the original Tailscale identity file (tsnet/tailscaled.state) from backup to reconnect at the same address."
        )
    filesystems = datasets()
    services = report["services"]
    service_ds = [ds for ds, (mp, _) in filesystems.items() if mp == services]
    if len(service_ds) > 1 or (
        not service_ds and services != report["data"] + "/services"
    ):
        raise SetupError(
            "Import the original services filesystem at its saved mountpoint."
        )
    layout = dict(
        hostId=host_id,
        data=location(dataset, report["data"]),
        services=location(service_ds[0] if service_ds else None, services),
    )
    bridges = bridges or {}
    parents = {p for r in report["workloads"] for p in r.get("parents", [])}
    physical = set(discovery_interfaces({}))
    for parent in parents:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", parent):
            raise SetupError("Unsupported saved LAN parent interface.")
        if parent not in physical and parent not in bridges:
            raise SetupError(f"Select an uplink for {parent}.")
    if (
        set(bridges) - parents
        or set(bridges) & physical
        or set(bridges.values()) - physical
        or len(set(bridges.values())) != len(bridges)
    ):
        raise SetupError("The selected bridge uplinks changed or overlap.")
    if bridges:
        layout["bridges"] = bridges
    catch_root = str(Path(services) / "catch")
    extras, units, accounts = {}, {}, set()
    compose = [r["name"] for r in report["workloads"] if r["kind"] == "docker-compose"]
    if compose:
        from engine import compose_storage

        mounts, extras = compose_storage(filesystems, engine, compose)
        layout["engineStorage"] = mounts
        extras.update({loc["dataset"]: loc for loc in mounts.values()})
    boot_units = {}
    for row in report["workloads"]:
        name = row["name"]
        if row["problems"]:
            raise SetupError(f"{name}: {row['problems'][0]}")
        for ds in row.get("filesystems", []):
            extras[ds] = location(ds, filesystems[ds][0])
        owner = row["dataset"]
        if owner != dataset and (
            not service_ds
            or not (owner == service_ds[0] or owner.startswith(service_ds[0] + "/"))
        ):
            extras[owner] = location(owner, filesystems[owner][0])
        if name == "catch":
            layout["legacyCatch"] = True
            if row.get("catchName"):
                layout["catchHostname"] = row["catchName"]
            continue
        if row["kind"] == "vm":
            svc = row["vmRecord"]
            if not (svc.get("VM") or {}).get("Components"):
                raise SetupError(
                    f"{name} needs its legacy VM runtime converted to a saved component descriptor."
                )
            if not Path("/dev/kvm").exists():
                raise SetupError(f"{name} requires KVM on this host.")
            unit = "yeet-vm-" + name + ".service"
            if unit in units:
                raise SetupError(f"Overlapping generated unit {unit}.")
            units[unit] = (
                run(
                    "catch",
                    "vm-recovery-unit",
                    report["data"],
                    services,
                    stdin=json.dumps(svc),
                )
                + "\n"
            )
            boot_units[name] = unit
            continue
        if row["kind"] not in ("docker-compose", "systemd"):
            raise SetupError(f"Unsupported workload type for {name}.")
        if row["sandbox"].get("ReadOnly") or row["sandbox"].get("Writable"):
            raise SetupError(
                f"{name} has external sandbox mounts that require an explicit storage mapping."
            )
        current = payload_units(row, catch_root)
        if units.keys() & current.keys():
            raise SetupError(f"Overlapping generated units for {name}.")
        units.update(current)
        if row["kind"] == "docker-compose":
            continue
        timer = name + ".timer"
        boot_units[name] = timer if timer in current else name + ".service"
        account = row["identity"]
        if (
            account.get("RequestedUser") == "yeet-svc"
            and account.get("RequestedGroup") == "yeet-svc"
        ):
            accounts.add((account.get("UID"), account.get("GID")))
        elif any("=yeet-svc" in l for l in current[name + ".service"].splitlines()):
            raise SetupError(
                f"{name} needs its original yeet-svc numeric user and group IDs."
            )
    helpers = [
        n
        for n in units
        if n.startswith("yeet-") and n.endswith(("-ns.service", "-ts.service"))
    ]
    if helpers or any(r["kind"] == "vm" for r in report["workloads"]):
        layout["migrationNetwork"] = True
        layout["networkUnits"] = sorted(helpers)
        docker_helpers = [
            f"yeet-{name}-ns.service"
            for name in compose
            if f"yeet-{name}-ns.service" in units
        ]
        units.update(network_units(report["data"], units, docker_helpers))
    volumes = sorted(
        {
            r["disk"].removeprefix("/dev/zvol/")
            for r in report["workloads"]
            if r.get("disk", "").startswith("/dev/zvol/")
        }
    )
    if volumes:
        layout["vmVolumes"] = volumes
    if len(accounts) > 1 or any(
        type(v) is not int or v <= 0 for pair in accounts for v in pair
    ):
        raise SetupError(
            "The native service user and group IDs conflict or are missing."
        )
    if accounts:
        layout["serviceUid"], layout["serviceGid"] = accounts.pop()
    if (
        not isinstance(enabled, list)
        or len(set(enabled)) != len(enabled)
        or set(enabled) - boot_units.keys()
    ):
        raise SetupError("A selected workload is missing. Review boot choices again.")
    layout["extraFilesystems"] = [
        loc
        for ds, loc in extras.items()
        if ds != dataset
        and (
            not service_ds
            or (ds != service_ds[0] and not ds.startswith(service_ds[0] + "/"))
        )
    ]
    validate_layout(layout, recover=True)
    private_hash = hashlib.sha256(
        json.dumps(units, sort_keys=True).encode()
    ).hexdigest()
    return (
        dict(
            hostname=hostname,
            layout=layout,
            service_count=len(report["workloads"]),
            unit_digest=private_hash,
        ),
        units,
        boot_units,
    )


def stage_units(data, units, boot_units, enabled, vendor, helpers):
    # Build the tree privately. Publishing it and the manifest is the last install step.
    for name in units:
        if os.path.lexists(vendor / name):
            raise SetupError(f"The new NixOS generation already defines {name}.")
    stage = tempfile.TemporaryDirectory(prefix=".yeet-adopt-", dir=data)
    try:
        destination = Path(stage.name, "host-systemd/etc/systemd/system")
        destination.mkdir(parents=True)
        for name, contents in units.items():
            path = destination / name
            path.parent.mkdir(exist_ok=True)
            path.touch(mode=0o600, exist_ok=False)
            path.write_text(contents)
        for unit in sorted(set([boot_units[name] for name in enabled] + helpers)):
            parent = destination / (
                "timers.target.wants"
                if unit.endswith(".timer")
                else "multi-user.target.wants"
            )
            parent.mkdir(exist_ok=True)
            (parent / unit).symlink_to("../" + unit)
    except BaseException:
        stage.cleanup()
        raise
    return stage


def review(ui, dataset):
    report = inventory(dataset)
    rows = report["workloads"]
    identity = "found" if report["identity"] else "missing"
    while True:
        idx = ui.choose(
            "RECOVERY",
            "What did Yeet find on your disks?",
            f"{report['data']} · Tailscale identity {identity}. Select an item to see its files, or continue to choose what starts at boot.",
            ["Return to recovery", "Continue to startup settings"]
            + [
                (
                    "Yeet host service"
                    if r["name"] == "catch"
                    else f"{r['name']}   {r['kind']}"
                )
                for r in rows
            ],
        )
        if not idx:
            return
        if idx == 1:
            from storage import host_id

            hid = host_id()
            # Preflight all services before asking for settings or an erase target.
            engine = {}
            if any(r["kind"] == "docker-compose" for r in rows):
                names = list(datasets())
                for role in ["docker", "containerd"]:
                    engine[role] = names[
                        ui.choose(
                            "CONTAINER STORAGE",
                            f"Original {role} storage",
                            f"Select the original /var/lib/{role} filesystem. Migration reuses containerd-backed Docker containers (overlayfs) and their stored restart policies.",
                            names,
                        )
                    ]
            physical = discovery_interfaces({})
            parents = sorted(
                {p for r in rows for p in r.get("parents", [])} - set(physical)
            )
            bridges = {}
            for bridge in parents:
                choices = [nic for nic in physical if nic not in bridges.values()]
                if not choices:
                    raise SetupError(
                        f"No unused physical Ethernet uplink for {bridge}."
                    )
                bridges[bridge] = choices[
                    ui.choose(
                        "PAYLOAD LAN",
                        f"Recreate {bridge}",
                        "Choose the physical Ethernet uplink for the saved payload bridge.",
                        choices,
                    )
                ]
            record, _, choices = adoption(dataset, "", hid, [], engine, bridges)
            name = ui.field(
                "MIGRATION",
                "Name the installed host",
                "Use the previous hostname if you want to keep its LAN name.",
                record["layout"].get("catchHostname", "yeet-home"),
            )
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
                raise SetupError("Enter a valid lowercase hostname.")
            layout = record["layout"]
            mappings = [
                f"{loc['dataset']}   {loc['path']}"
                for loc in [
                    layout["data"],
                    layout["services"],
                    *layout["extraFilesystems"],
                ]
                if loc.get("dataset")
            ]
            mappings += [
                f"{role}   {loc['dataset']} at /var/lib/{role}"
                for role, loc in layout.get("engineStorage", {}).items()
            ]
            mappings += [f"{bridge}   uplink {nic}" for bridge, nic in bridges.items()]
            while True:
                item = ui.choose(
                    "STORAGE REVIEW",
                    "Check storage and network settings",
                    f"You’ll reach Yeet at {layout.get('catchHostname', name)} on Tailscale. Check where your files will be mounted. Continue only if you trust the startup files on these disks.",
                    ["Use these settings"] + mappings,
                )
                if not item:
                    break
                ui.choose(
                    "STORAGE AND NETWORK", "Saved setting", mappings[item - 1], ["Back"]
                )
            enabled = ui.boot_choices(choices)
            if ui.choose(
                "MIGRATION",
                "Reconnect as your old Yeet host?",
                "Shut down the old host first. The replacement will use its Tailscale identity and start the apps you selected.",
                [
                    "Back to the app list",
                    "Old host is offline. Choose replacement disk",
                ],
            ):
                record["hostname"] = name
                record["legacy"] = {
                    "enabled": enabled,
                    "engine": engine,
                    "bridges": bridges,
                    "unit_digest": record["unit_digest"],
                }
                return dataset, record
            continue
        row = rows[idx - 2]
        details = [row["root"], row["dataset"] or "No backing dataset found"]
        if row.get("disk"):
            details.append("VM disk   " + row["disk"])
        if row.get("parents"):
            details.append("Network parent   " + ", ".join(row["parents"]))
        details.extend(row["problems"])
        if row["units"]:
            details.append("Saved startup files   " + ", ".join(sorted(row["units"])))
        if row["kind"] == "docker-compose":
            details.append(
                "Restore Docker and containerd storage, named volumes and bind mounts."
            )
        elif row["kind"] == "vm":
            details.append("Recreate the VM launcher and its network on the new host.")
        if row["kind"] == "systemd":
            details.append(
                "Check your backup for custom startup settings or schedules."
            )
        ui.choose(
            "APP DETAILS",
            "Yeet host service" if row["name"] == "catch" else row["name"],
            "\n".join(details),
            ["Back to the app list"],
        )
