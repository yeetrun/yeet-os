import json
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installer"))
import update
from system_build import FACTS, build_system


class UpdateTests(unittest.TestCase):
    def test_boot_status_compares_with_booted_generation_after_a_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            booted, active = root / "booted", root / "active"
            for system in (booted, active):
                system.mkdir()
                (system / "kernel-params").write_text("quiet")
                (system / "kernel").symlink_to(root / "old-kernel")
            (root / "old-kernel").touch()
            (root / "new-kernel").touch()
            with patch.object(update, "BOOTED", booted):
                self.assertEqual(update.boot_changes(active), [])
                (active / "kernel").unlink()
                (active / "kernel").symlink_to(root / "new-kernel")
                (active / "kernel-params").write_text("debug")
                self.assertEqual(update.boot_changes(active), ["kernel", "kernel parameters"])

    def test_only_changed_shared_nixos_inhibitors_block_live_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            running, target = Path(tmp) / "old", Path(tmp) / "new"
            running.mkdir()
            target.mkdir()
            (running / "switch-inhibitors").write_text('{"dbus":"broker","gone":1}')
            (target / "switch-inhibitors").write_text('{"dbus":"classic","added":2}')
            with patch.object(update, "RUNNING", running):
                self.assertEqual(update.changed_inhibitors(target), ["dbus"])
                (target / "switch-inhibitors").unlink()
                self.assertEqual(update.changed_inhibitors(target), [])

    def test_latest_manifest_uses_tag_pinned_assets_and_checks_before_import(self):
        data = {
            "format": 1,
            "system": update.SYSTEM,
            "version": "1.2.3",
            **{
                part: {
                    "path": "/nix/store/" + "0" * 32 + "-" + part,
                    "sha256": "0" * 64,
                }
                for part in update.PARTS
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def download(url, dest):
                if url.endswith(".json"):
                    dest.write_text(json.dumps(data))
                else:
                    dest.write_bytes(b"truncated download")

            with patch.object(update, "fetch", side_effect=download) as fetch:
                release = update.metadata(None, root)
                fetch.assert_called_once_with(
                    f"{update.RELEASES}/latest/download/yeet-os-{update.SYSTEM}.json",
                    root / "manifest.json",
                )
                with (
                    patch.object(
                        update.subprocess, "run", return_value=CompletedProcess([], 1)
                    ),
                    patch.object(update.subprocess, "Popen") as decoder,
                    self.assertRaisesRegex(ValueError, "Checksum mismatch"),
                ):
                    update.restore_parts(release, None, root)
                decoder.assert_not_called()
                name = update.asset("source", update.SYSTEM)
                self.assertEqual(
                    fetch.call_args.args,
                    (f"{update.RELEASES}/download/v1.2.3/{name}", root / name),
                )

    def test_legacy_facts_and_local_imports_survive_without_copying_the_old_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "nixos"
            local = old / "local"
            local.mkdir(parents=True)
            (local / "default.nix").write_text("{ imports = [ ./extra.nix ]; }")
            (local / "extra.nix").write_text('{ networking.hostName = "workshop"; }')
            (old / "flake.nix").write_text('base = os.lib.installed machine "bios";')
            (old / "machine.json").write_text("null")
            (old / "host.json").write_text('{"hostname":"home","interfaces":[]}')
            (old / "layout.json").write_text("null")
            (old / "private.key").write_text("exclude")
            with (
                patch.object(update, "SITE", old),
                patch.object(update, "RUNNING", root),
            ):
                site = update.machine_site(root)
            self.assertEqual(json.loads((site / "firmware.json").read_text()), "bios")

            def inspect(*argv):
                flake = next(arg for arg in argv if "#nixosConfigurations" in arg)
                staged = Path(flake.split("#")[0].removeprefix("path:"))
                self.assertEqual(
                    {p.name for p in staged.iterdir()},
                    {*FACTS, "flake.nix", "platform.json", "local"},
                )
                self.assertEqual(
                    (staged / "local/extra.nix").read_bytes(),
                    (local / "extra.nix").read_bytes(),
                )
                self.assertNotIn(
                    "os.lib.installed machine", (staged / "flake.nix").read_text()
                )
                return "system"

            self.assertEqual(
                build_system(inspect, "/os", "/pkgs", site, local=local), "system"
            )
