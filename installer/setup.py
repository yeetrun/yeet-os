#!/usr/bin/env python3
import argparse
import contextlib
import curses
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
from urllib.parse import urlparse

from provision import (
    SetupError,
    disks,
    existing_host,
    install,
    locked,
    machine_profile,
    run,
)

CLIENT_GUIDE = "https://yeetrun.com/docs/getting-started/host-setup"
CLIENT_INSTALL = "curl -fsSL https://yeetrun.com/install.sh | sh"


class Cancelled(Exception):
    pass


def qr_matrix(url):
    xpm = run("qrencode", "-t", "XPM", "-s", "1", "-m", "4", "-o", "-", url)
    lines = re.findall(r'^\s*"([^"]*)"', xpm, re.M)
    width, height, colors, stride = map(int, lines[0].split())
    dark = {
        s[0]
        for s in lines[1 : 1 + colors]
        if "#000000" in s.lower() or "black" in s.lower()
    }
    if stride != 1 or not dark:
        raise SetupError("QR encoder returned an unsupported palette.")
    return [
        [cell in dark for cell in row[:width]]
        for row in lines[1 + colors : 1 + colors + height]
    ]


class Console:
    def __init__(self, screen, escape_hint="Main menu"):
        self.escape_hint = escape_hint
        self.s = screen
        screen.keypad(True)
        curses.noecho()
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        for pair, fg, bg in [
            (1, curses.COLOR_CYAN, -1),
            (2, curses.COLOR_BLACK, curses.COLOR_CYAN),
            (3, curses.COLOR_WHITE, -1),
            (4, curses.COLOR_BLACK, curses.COLOR_WHITE),
        ]:
            curses.init_pair(pair, fg, bg)
        self.step = ""
        self.footer = ""

    def put(self, y, x, value, style=0):
        h, w = self.s.getmaxyx()
        if 0 <= y < h and x < w - 1:
            self.s.addnstr(y, x, value, w - x - 1, style)

    def page(self, step, title, copy="", footer=""):
        self.s.erase()
        h, w = self.s.getmaxyx()
        self.put(1, 3, " YEET OS ", curses.color_pair(2) | curses.A_BOLD)
        self.put(1, max(14, w - len(step) - 4), step, curses.color_pair(1))
        self.put(2, 3, "─" * max(10, w - 7), curses.color_pair(1))
        self.put(4, 4, title, curses.color_pair(3) | curses.A_BOLD)
        lines = [
            part
            for line in copy.splitlines()
            for part in textwrap.wrap(line, max(24, min(76, w - 9)))
        ]
        for n, line in enumerate(lines):
            self.put(6 + n, 4, line)
        self.put(h - 2, 4, footer, curses.color_pair(1))
        self.s.refresh()
        return 8 + len(lines)

    def choose(self, step, title, copy, choices):
        index = 0
        while True:
            y = self.page(
                step,
                title,
                copy,
                f"↑ ↓  Select      Enter  Continue      Esc  {self.escape_hint}",
            )
            h, _ = self.s.getmaxyx()
            count = max(1, (h - 3 - y) // 2)
            start = max(0, index - count + 1)
            for n, label in enumerate(choices[start : start + count], start):
                selected = n == index
                self.put(
                    y + (n - start) * 2,
                    4,
                    (" ›  " if selected else "    ") + label + "  ",
                    curses.color_pair(2) if selected else 0,
                )
            self.s.refresh()
            key = self.s.getch()
            if key in (curses.KEY_UP, ord("k")):
                index = (index - 1) % len(choices)
            elif key in (curses.KEY_DOWN, ord("j")):
                index = (index + 1) % len(choices)
            elif key in (10, 13, curses.KEY_ENTER):
                return index
            elif key == 27:
                raise Cancelled()

    def boot_choices(self, choices):
        names = list(choices)
        enabled = set()
        index = 0
        while names:
            top = self.page(
                "BOOT SETTINGS",
                "What should start when the host boots?",
                "Select the apps, scheduled jobs and VMs you want Yeet to start. Docker keeps each container’s restart policy.",
                "↑ ↓  Select    Space  Toggle    A  Toggle all    Enter  Accept    Esc  Back",
            )
            count = max(1, self.s.getmaxyx()[0] - top - 3)
            start = max(0, index - count + 1)
            for n, name in enumerate(names[start : start + count], start):
                self.put(
                    top + n - start,
                    4,
                    ("[x] " if name in enabled else "[ ] ") + name,
                    curses.color_pair(2) if n == index else 0,
                )
            self.s.refresh()
            key = self.s.getch()
            if key in (curses.KEY_UP, ord("k")):
                index = (index - 1) % len(names)
            elif key in (curses.KEY_DOWN, ord("j")):
                index = (index + 1) % len(names)
            elif key == ord(" "):
                enabled.symmetric_difference_update([names[index]])
            elif key in (ord("a"), ord("A")):
                enabled = set() if len(enabled) == len(names) else set(names)
            elif key in (10, 13, curses.KEY_ENTER):
                break
            elif key == 27:
                raise Cancelled()
        return sorted(enabled)

    def field(self, step, title, copy, initial="", secret=False):
        value = initial
        try:
            curses.curs_set(1)
            while True:
                y = self.page(
                    step,
                    title,
                    copy,
                    f"Enter  Continue      Backspace  Delete      Esc  {self.escape_hint}",
                )
                text = "*" * len(value) if secret else value
                self.put(y, 4, "› " + text, curses.color_pair(1) | curses.A_BOLD)
                self.s.move(y, min(self.s.getmaxyx()[1] - 2, 6 + len(text)))
                key = self.s.get_wch()
                if key in ("\n", "\r", curses.KEY_ENTER):
                    return value
                if key == "\x1b":
                    raise Cancelled()
                if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    value = value[:-1]
                elif isinstance(key, str) and key.isprintable() and len(value) < 255:
                    value += key
        finally:
            curses.curs_set(0)

    def qr(self, step, title, url, labels, footer):
        matrix = qr_matrix(url)
        self.s.erase()
        h, w = self.s.getmaxyx()
        side = len(matrix[0])
        qrrows = (len(matrix) + 1) // 2
        if w < side + 28 or h < qrrows + 3:
            self.page(step, title, url, footer)
            self.s.refresh()
            return
        self.put(0, 2, " YEET OS ", curses.color_pair(2) | curses.A_BOLD)
        self.put(0, 13, step, curses.color_pair(1))
        top = max(2, (h - qrrows) // 2)
        for i in range(0, len(matrix), 2):
            # In a Linux text console each half-block is one square QR module.
            row = "".join(
                (" ", "▄", "▀", "█")[2 * int(a) + int(b)]
                for a, b in zip(
                    matrix[i], matrix[i + 1] if i + 1 < len(matrix) else [False] * side
                )
            )
            self.put(top + i // 2, 2, row, curses.color_pair(4))
        col = side + 5
        self.put(3, col, title, curses.A_BOLD)
        line = 5
        for label in labels:
            for part in textwrap.wrap(label, max(15, w - col - 3)):
                self.put(line, col, part)
                line += 1
            line += 1
        self.put(h - 1, 2, footer, curses.color_pair(1))
        self.s.refresh()

    def event(self, event, detail):
        if event == "poll":
            self.s.timeout(0)
            try:
                key = self.s.getch()
            finally:
                self.s.timeout(-1)
            if key in (ord("k"), ord("K")):
                while True:
                    path = self.field(
                        "TAILNET ENROLLMENT",
                        "Use a tagged key file",
                        "Enter the path to a single-use, persistent tag:catch key on this machine.",
                        "/run/yeet-authkey",
                    )
                    if Path(path).is_file():
                        return Path(path)
                    self.page(
                        "TAILNET ENROLLMENT",
                        "File unavailable",
                        path,
                        "Enter  Try again",
                    )
                    self.s.getch()
        elif event == "pair":
            if urlparse(detail).scheme != "https":
                raise SetupError("Expected an HTTPS login address.")
            self.qr(
                "TAILNET ENROLLMENT",
                "Scan to sign in",
                detail,
                [
                    "Open your phone camera and scan the code.",
                    "Approve this host in your Tailscale account. Your account needs permission to apply tag:catch.",
                    detail,
                ],
                "Waiting for approval…      K  Use key file",
            )
        else:
            self.page(
                "INSTALLING",
                "Setting up your host",
                detail,
                "Keep the installer attached until setup finishes.",
            )

    def clients(self, name, live):
        self.qr(
            "WORKSTATION SETUP",
            "Your Yeet client",
            CLIENT_GUIDE,
            [
                "Use a computer on the same tailnet.",
                "Scan for the client installation guide.",
                name,
            ],
            "Enter  Show commands",
        )
        self.s.getch()
        y = self.page(
            "WORKSTATION SETUP",
            "Deploy your first payload",
            "On your Mac or Linux workstation, sign into this tailnet and run these commands.",
            "Enter  Reboot into Yeet OS" if live else "Enter  Return to shell",
        )
        for i, cmd in enumerate(
            (
                CLIENT_INSTALL,
                "yeet config --host=" + name,
                "yeet version",
                "yeet run web nginx:alpine -p 8080:80",
            )
        ):
            self.put(y + 2 * i, 4, cmd, curses.color_pair(1))
        self.s.refresh()
        key = self.s.getch()
        if live and key in (10, 13, curses.KEY_ENTER):
            run("systemctl", "reboot")

    def wizard(self):
        live = "YEET_UEFI_SYSTEM" in os.environ
        if not live:
            with locked():
                self.clients(existing_host(self.event), False)
            return
        while True:
            try:
                from storage_ui import fresh_layout, recover

                machine = machine_profile(self)
                choice = self.choose(
                    "INSTALLER",
                    "Set up Yeet OS",
                    "Choose what to do with this machine. Storage is reviewed before any disk is erased.",
                    [
                        "Install a fresh host",
                        "Recover an existing host",
                        "Open a rescue shell",
                    ],
                )
                if choice == 2:
                    return
                recovery = recover(self) if choice == 1 else None
                if choice == 1 and recovery is None:
                    continue
                devices = disks()
                if not devices:
                    raise SetupError(
                        "No unused disk of at least 10 GiB is available. The boot media is excluded."
                    )
                sel = self.choose(
                    "1 / 4   STORAGE",
                    "Choose the installation disk",
                    "Setup will replace every partition on the selected disk.",
                    [
                        f"{d['path']}   {d['size'] / 1024**3:.1f} GiB   {d['model'] or ''} {d['serial'] or ''}"
                        for d in devices
                    ],
                )
                dev = devices[sel]
                disk = dev["path"]
                name = (
                    recovery[1]["hostname"]
                    if recovery
                    else machine["hostname"]
                    if machine
                    else self.field(
                        "IDENTITY",
                        "Name your host",
                        "Choose the name you will use from your workstation.",
                        "yeet-home",
                    )
                )
                layout = (
                    recovery[1]["layout"]
                    if recovery
                    else fresh_layout(self, name, machine)
                )
                password = ""
                if not machine:
                    password = self.field(
                        "LOCAL ACCESS",
                        "Set a console password",
                        "Use this password to sign in locally as root.",
                        secret=True,
                    )
                    if not password or password != self.field(
                        "LOCAL ACCESS",
                        "Confirm your password",
                        "Enter your console password again.",
                        secret=True,
                    ):
                        raise SetupError(
                            "The passwords must match and contain at least one character."
                        )
                summary = [
                    f"Erase {disk}   {dev['model'] or 'Disk'}   {dev['size'] / 1024**3:.1f} GiB",
                    f"Host   {name}",
                ]
                for label, loc in (
                    (
                        ("Yeet host data", layout["data"]),
                        ("App files", layout["services"]),
                    )
                    if layout
                    else (
                        ("Yeet host data", {"path": "/var/lib/yeet"}),
                        ("App files", {"path": "/var/lib/yeet/services"}),
                    )
                ):
                    action = (
                        "Preserve"
                        if recovery
                        else "Create"
                        if loc.get("create")
                        else "Use"
                    )
                    summary.append(
                        f"{label}   {action} {loc.get('dataset', loc['path'])}"
                    )
                if layout and layout.get("engineStorage"):
                    summary.append(
                        "Reuse the selected Docker and containerd filesystems."
                    )
                elif layout and layout["data"].get("dataset"):
                    summary.append(
                        "Docker volumes and images will be stored with your Yeet host data."
                    )
                if recovery:
                    summary.append(
                        "The original host must be offline before this identity starts."
                    )
                confirm = self.field(
                    "REVIEW",
                    "Review installation",
                    "\n".join(summary + [f"Type {disk} to erase the OS disk."]),
                )
                plan = dict(
                    version=1,
                    disk=disk,
                    erase=confirm,
                    serial=dev["serial"],
                    hostname=name,
                    password=password,
                    layout=layout,
                    ssh_public_key=machine["sshPublicKey"] if machine else "",
                    source_offline=bool(recovery),
                    recover_dataset=recovery[0] if recovery else None,
                    legacy=recovery[1].get("legacy") if recovery else None,
                )
                with locked():
                    result = install(plan, None, self.event)
                self.clients(result, True)
                return
            except Cancelled:
                continue
            except (SetupError, OSError, ValueError, KeyError) as exc:
                self.page(
                    "SETUP", "Setup needs attention", str(exc), "Enter  Return to setup"
                )
                self.s.getch()


@contextlib.contextmanager
def seed_file(path):
    if path:
        yield Path(path).resolve()
        return
    with tempfile.TemporaryDirectory(prefix="yeet-seed-") as mnt:
        run("mount", "-o", "ro,nosuid,nodev,noexec", "/dev/disk/by-label/YEETSEED", mnt)
        try:
            yield Path(mnt, "install.json")
        finally:
            run("umount", mnt)


def unattended(path):
    with seed_file(path) as seed:
        config = json.loads(seed.read_text())
        key = (seed.parent / config["auth_key_file"]).resolve()
        if (
            not key.is_relative_to(seed.parent)
            or not key.is_file()
            or not key.read_text().strip()
        ):
            raise SetupError("Supply a valid auth_key_file alongside the install plan.")
        action = config.get("on_success", "reboot")
        if action not in ("reboot", "poweroff", "stay"):
            raise SetupError("Unknown on_success action.")
        config["unattended"] = True

        def progress(event, detail):
            if event == "progress":
                print(detail, flush=True)

        name = install(config, key, progress)
        print(
            f"Yeet OS installed. On your workstation run: yeet config --host={name}",
            flush=True,
        )
    if action != "stay":
        run("systemctl", action)


def main():
    args = argparse.ArgumentParser()
    args.add_argument(
        "--auto", action="store_true", help="install from YEETSEED/install.json"
    )
    args.add_argument("--config", help="explicit unattended JSON plan")
    args.add_argument("--select-uplink", action="store_true", help=argparse.SUPPRESS)
    options = args.parse_args()
    if os.geteuid() != 0:
        raise SetupError("Run yeet-setup as root.")
    if options.select_uplink:
        machine_profile()
        return
    if options.auto or options.config:
        if "YEET_UEFI_SYSTEM" not in os.environ:
            raise SetupError("Boot the installer media for a disk install.")
        with locked():
            unattended(options.config)
    else:
        tty = os.ttyname(0) if os.isatty(0) else ""
        if re.fullmatch(r"/dev/tty[0-9]+", tty):
            size = os.get_terminal_size()
            if size.columns >= 150 and size.lines >= 46:
                subprocess.run(["setfont", "-C", tty, "-d"], capture_output=True)
        if os.environ.get("TERM") in ("", "dumb", None) and os.isatty(0):
            os.environ["TERM"] = "xterm"
        curses.wrapper(lambda screen: Console(screen).wizard())


if __name__ == "__main__":
    try:
        main()
    except (SetupError, OSError, ValueError, KeyError) as exc:
        sys.exit(str(exc))
    except (Cancelled, KeyboardInterrupt):
        sys.exit(130)
