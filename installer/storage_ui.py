"""Storage screens shared by the local and SSH consoles."""

import curses
import os
from pathlib import Path
import shutil
import subprocess

from provision import SetupError, locked, run
from storage import (
    NoSavedSettings,
    datasets,
    host_id,
    importable,
    location,
    os_disks,
    recovery_descriptor,
    repair_os,
    validate_layout,
)


def pool_tools(ui):
    if not shutil.which("zpool"):
        raise SetupError(
            "This media does not include ZFS tools. Boot a prepared Yeet OS image."
        )
    choices = [
        "Return to storage choices",
        "Import an existing pool",
        "Open a ZFS shell",
    ]
    choice = ui.choose(
        "STORAGE TOOLS",
        "Manage ZFS pools",
        "Import a pool by its ID, or use the shell to create a pool with your preferred disk layout.",
        choices,
    )
    if choice == 1:
        pools = importable()
        if not pools:
            raise SetupError("No exported pools were detected.")
        index = ui.choose(
            "EXISTING POOLS",
            "Import a pool",
            "Select an offline pool. Setup imports it without mounting its datasets.",
            [f"{name}   {guid}" for name, guid in pools],
        )
        name, guid = pools[index]
        if (
            ui.choose(
                "CONFIRM IMPORT",
                f"Import {name}?",
                f"Pool ID {guid}. Confirm that the pool is no longer in use on another host.",
                ["Go back", "Import pool"],
            )
            == 1
        ):
            try:
                run("zpool", "import", "-N", guid)
            except SetupError as exc:
                if "previously in use" not in str(exc):
                    raise
                confirm = ui.field(
                    "OFFLINE POOL",
                    f"Take ownership of {name}?",
                    "ZFS reports that another system last used this pool. Power off that system and disconnect any other writer. Type the pool name to force an unmounted import, or leave it blank to cancel.",
                )
                if confirm == name:
                    run("zpool", "import", "-N", "-f", guid)
                elif confirm:
                    raise SetupError(
                        "Pool name did not match. The pool was left alone."
                    )

    elif choice == 2:
        curses.def_prog_mode()
        curses.endwin()
        try:
            print(
                "\nYeet OS storage shell\nUse lsblk, zpool and zfs to prepare storage. Type exit to rescan and return.\n",
                flush=True,
            )
            subprocess.run(["bash", "-i"], env=dict(os.environ, YEET_INSTALL_SHELL="1"))
        finally:
            curses.reset_prog_mode()
            ui.s.clear()
            ui.s.refresh()


def choose_dataset(ui, label, suggested):
    while True:
        rows = datasets()
        entries = list(rows)
        idx = ui.choose(
            "ZFS STORAGE",
            label,
            "Select an existing filesystem. You can allocate a new child under it or use an empty dataset.",
            [f"{k}   {rows[k][1] / 1024**3:.1f} GiB free" for k in entries]
            + ["Manage pools or open shell"],
        )
        if idx == len(entries):
            pool_tools(ui)
            continue
        ds = entries[idx]
        point = rows[ds][0]
        mode = ui.choose(
            "ZFS STORAGE",
            ds,
            point,
            ["Create a new child dataset", "Use this empty dataset"],
        )
        if mode == 0:
            child = ui.field(
                "ZFS STORAGE",
                "Name the new dataset",
                f"Parent dataset {ds}.",
                suggested,
            )
            if not child or "/" in child or child in (".", ".."):
                raise SetupError("Enter a single dataset name.")
            return location(ds + "/" + child, str(Path(point) / child), True)
        return location(ds, point)


def choose_location(ui, label, default, suggested, help_text):
    mode = ui.choose(
        "YEET STORAGE",
        label,
        help_text,
        ["Use " + default, "Choose another directory", "Choose a ZFS dataset"],
    )
    if mode == 0:
        return location(None, default)
    if mode == 1:
        return location(
            None,
            ui.field(
                "YEET STORAGE",
                label,
                "Enter the absolute path on the installed machine.",
                default,
            ),
        )
    return choose_dataset(ui, label, suggested)


def fresh_layout(ui, name, machine):
    while True:
        choice = ui.choose(
            "PAYLOAD STORAGE",
            "Where should Yeet keep its data?",
            "Yeet saves this host’s identity and a list of your apps. Each app also has its own files. Choose where to keep them.",
            [
                "Keep both on the OS disk",
                "Choose where to store host data and app files",
                "Manage ZFS pools or open shell",
            ],
        )
        if choice == 0:
            return None
        if choice == 2:
            pool_tools(ui)
            continue
        data = choose_location(
            ui,
            "Yeet host data",
            "/var/lib/yeet",
            f"{name}-data",
            "Yeet keeps its Tailscale identity, deployment history and cached images here.",
        )
        services = choose_location(
            ui,
            "App files",
            data["path"] + "/services",
            f"{name}-services",
            "Choose a dedicated ZFS filesystem to give each service a child dataset for snapshots and VM clones.",
        )
        result = dict(
            data=data,
            services=services,
            hostId=machine["hostId"] if machine else host_id(),
        )
        validate_layout(result)
        return result


def recover(ui):
    while True:
        choice = ui.choose(
            "RECOVERY",
            "Recover an existing Yeet host",
            "If you replaced the OS disk, import your original ZFS pools, then choose Restore Yeet from my data disks.",
            [
                "Repair my Yeet OS boot disk",
                "Restore Yeet from my data disks",
                "Manage ZFS pools or open shell",
            ],
        )
        if choice == 2:
            pool_tools(ui)
            continue
        if choice == 0:
            hosts = os_disks()
            if not hosts:
                raise SetupError("No unmounted Yeet OS boot disk was found.")
            idx = ui.choose(
                "BOOT REPAIR",
                "Select the installed host",
                "Reinstall its bootloader using its saved NixOS generation. Existing filesystems and host identity are preserved.",
                [f"{h['hostname']}   {h['disk']}   {h['serial'] or ''}" for h in hosts],
            )
            h = hosts[idx]
            if (
                ui.field(
                    "BOOT REPAIR",
                    "Confirm boot repair",
                    "Type the hostname to reinstall its bootloader.",
                )
                != h["hostname"]
            ):
                raise SetupError("Hostname confirmation did not match.")
            with locked():
                repair_os(h)
            ui.page(
                "RECOVERY",
                "Bootloader restored",
                "Detach the installation media and boot the original disk.",
                "Enter  Return",
            )
            ui.s.getch()
            return None
        rows = datasets()
        if not rows:
            raise SetupError(
                "Choose Manage ZFS pools or open shell and import the pool that holds your Yeet data."
            )
        names = list(rows)
        ds = names[
            ui.choose(
                "DATASET RECOVERY",
                "Where is your Yeet host data?",
                "Choose the ZFS dataset where Yeet kept its host data (the one with db.json). Setup reads it to find your settings and apps.",
                names,
            )
        ]
        try:
            record = recovery_descriptor(ds)
        except NoSavedSettings:
            from legacy import review

            result = review(ui, ds)
            if result:
                return result
            continue
        if (
            ui.choose(
                "RECOVERY",
                f"Recover {record['hostname']}?",
                "Yeet found your saved settings and app list. Shut down the old host before continuing. Next, choose the replacement OS disk.",
                ["Go back", "Old host is offline. Continue"],
            )
            == 1
        ):
            return ds, record
