"""Adapt the host-specific parts of active Yeet unit artifacts for NixOS."""

from pathlib import Path
import re
import shlex

from provision import SetupError, run

RUNNER = "/var/lib/yeet-host/run/catch"


def payload_units(row, catch_root):
    primary = row["name"]
    unit_names = {
        "systemd.service": primary + ".service",
        "systemd.timer": primary + ".timer",
        "netns.service": "yeet-" + primary + "-ns.service",
        "tailscale.service": "yeet-" + primary + "-ts.service",
    }
    result = {}
    root = Path(row["root"])
    for key, raw in row.get("contents", {}).items():
        if key == "systemd.service":
            raw = run("catch", "adapt-host-unit", stdin=raw)
        lines = []
        for line in raw.splitlines():
            directive, eq, value = line.partition("=")
            if directive in (
                "ExecStart",
                "ExecStartPre",
                "ExecStartPost",
                "ExecStop",
                "ConditionFileIsExecutable",
            ):
                words = shlex.split(value)
                program = Path(words[0]) if words else Path(".")
                if (
                    key == "systemd.service"
                    and directive == "ExecStart"
                    and row["sandbox"].get("State") == "on"
                    and program.name != "bwrap"
                ):
                    raise SetupError(
                        f"{primary} has a sandbox policy but its active unit lacks the sandbox launcher."
                    )
                if program == Path(catch_root, "run/catch") or (
                    program.parent == Path(catch_root, "bin")
                    and re.fullmatch(r"catch-[0-9]+", program.name)
                ):
                    value = value.replace(str(program), RUNNER, 1)
                elif program == Path(RUNNER):
                    pass
                elif (
                    program == Path("/run/current-system/sw/bin/bwrap")
                    and key == "systemd.service"
                ):
                    pass
                elif not program.is_relative_to(root) and program not in (
                    Path(catch_root, "data/service-ns"),
                    Path("/bin/systemctl"),
                    Path("/usr/bin/test"),
                ):
                    raise SetupError(
                        f"{primary} depends on {program}. Restore its original runtime."
                    )
                if ".." in program.parts:
                    raise SetupError(f"Invalid executable path for {primary}.")
            elif directive in ("User", "Group") and value not in ("root", "yeet-svc"):
                raise SetupError(
                    f"{primary} needs its original {directive.lower()} mapping."
                )
            elif directive in ("EnvironmentFile", "WorkingDirectory") and value != "/":
                path = Path(value.removeprefix("-"))
                if not path.is_relative_to(root) or ".." in path.parts:
                    raise SetupError(f"{primary} depends on an external {directive}.")
            lines.append(directive + eq + value)
        result[unit_names[key]] = "\n".join(lines) + "\n"
    if row["kind"] == "systemd" and primary + ".service" not in result:
        raise SetupError(f"{primary} is missing its active service unit.")
    return result


def network_units(data, catch_root, units, docker_helpers):
    result = {
        "yeet-ns.service": f"""[Unit]
Description=Yeet host network
Before=yeet-docker-prereqs.target docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
EnvironmentFile={catch_root}/data/yeet-ns.env
ExecStart=/usr/bin/env CATCH_BIN={RUNNER} {catch_root}/data/yeet-ns
""",
    }
    if any("yeet-iso-dns.service" in unit for unit in units.values()):
        result["yeet-iso-dns.service"] = f"""[Unit]
Requires=yeet-ns.service
After=yeet-ns.service

[Service]
ExecStart={RUNNER} -data-dir {data} iso-dns
Restart=on-failure
"""
    if docker_helpers:
        after = " ".join(["catch.service", "yeet-ns.service", *sorted(docker_helpers)])
        result["yeet-docker-prereqs.target"] = (
            f"[Unit]\nWants={after}\nAfter={after}\nBefore=docker.service\n"
        )
        result["docker.service.d/yeet.conf"] = (
            "[Unit]\nWants=yeet-docker-prereqs.target\nAfter=yeet-docker-prereqs.target\n"
        )
    return result
