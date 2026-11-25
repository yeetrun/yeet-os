"""Resolve the storage used by saved Compose containers before starting Docker."""

import json
from pathlib import Path

from provision import SetupError
from storage import location, view


def compose_storage(filesystems, engine, services):
    if not engine or set(engine) != {"docker", "containerd"}:
        raise SetupError("Choose the original Docker and containerd datasets.")
    mounts = {}
    for role, dataset in engine.items():
        if dataset not in filesystems:
            raise SetupError(f"Import the original {role} dataset.")
        mounts[role] = location(dataset, filesystems[dataset][0])
    if mounts["docker"] == mounts["containerd"]:
        raise SetupError("Select the dedicated filesystem for each runtime.")
    with view(engine["containerd"]) as root:
        marker = root / "io.containerd.metadata.v1.bolt/meta.db"
        if marker.resolve() != marker or not marker.is_file():
            raise SetupError("The containerd dataset is missing its metadata database.")
    projects, dependencies = set(), set()
    with view(engine["docker"]) as root:
        containers = root / "containers"
        if containers.resolve() != containers or not containers.is_dir():
            raise SetupError("The Docker dataset is missing its containers directory.")
        for path in sorted(containers.glob("*/config.v2.json")):
            if path.resolve() != path or not path.is_file():
                raise SetupError("Unexpected link in the Docker container metadata.")
            state = json.loads(path.read_bytes())
            # A driver switch leaves the previous driver's container records on disk.
            # Only adopt a project after locating its containerd-backed records below.
            if state.get("Driver") != "overlayfs":
                continue
            project = ((state.get("Config") or {}).get("Labels") or {}).get(
                "com.docker.compose.project"
            )
            if project:
                projects.add(project)
            for mount in (state.get("MountPoints") or {}).values():
                source = mount.get("Source", "")
                if mount.get("Type") == "volume":
                    if mount.get("Driver") != "local" or not source.startswith(
                        "/var/lib/docker/volumes/"
                    ):
                        raise SetupError(
                            "Restore the Docker volume driver and its original storage."
                        )
                    volume = root / source.removeprefix("/var/lib/docker/")
                    if volume.resolve() != volume or not volume.is_dir():
                        raise SetupError("A saved Docker named volume is missing.")
                elif mount.get("Type") == "bind" and source not in (
                    "/var/run/docker.sock",
                    "/run/docker.sock",
                ):
                    dependencies.add(source)
                elif mount.get("Type") not in ("bind", "tmpfs"):
                    raise SetupError(
                        "A saved container uses an unsupported mount type."
                    )
            hostconfig = path.with_name("hostconfig.json")
            if hostconfig.is_symlink() or not hostconfig.is_file():
                raise SetupError("A saved container is missing its host configuration.")
            settings = json.loads(hostconfig.read_bytes())
            for device in settings.get("Devices") or []:
                node = Path(device.get("PathOnHost", ""))
                if not node.is_relative_to("/dev") or not node.exists():
                    raise SetupError(f"A saved container needs hardware at {node}.")
    missing = sorted(s for s in services if "catch-" + s not in projects)
    if missing:
        raise SetupError(
            f"The selected Docker storage has no saved Compose project for {missing[0]}."
        )
    storage = {}
    for source in sorted(dependencies):
        path = Path(source)
        matches = [
            (len(Path(mp).parts), ds, mp)
            for ds, (mp, _) in filesystems.items()
            if path.is_relative_to(mp)
        ]
        if not matches or ".." in path.parts:
            raise SetupError(
                f"Restore the backing filesystem or original path mapping for {source}."
            )
        matches.sort(reverse=True)
        depth, dataset, point = matches[0]
        if len(matches) > 1 and matches[1][0] == depth:
            raise SetupError(
                f"Multiple datasets claim {source}. Resolve their mountpoints and rescan."
            )
        with view(dataset) as root:
            target = root / path.relative_to(point)
            if target.resolve() != target or not target.exists():
                raise SetupError(f"The saved bind mount {source} is missing.")
        storage[dataset] = location(dataset, point)
    return mounts, storage
