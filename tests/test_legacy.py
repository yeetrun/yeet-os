import contextlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installer"))
import engine
import legacy
import storage
from provision import SetupError


class LegacyMigrationTests(unittest.TestCase):
    def test_inventory_uses_active_generation_and_distinct_filesystems(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp).resolve() / "data"
            app = data.parent / "app"
            app.mkdir()
            (data / "tsnet").mkdir(parents=True)
            (data / "tsnet/tailscaled.state").touch()
            (app / "good").write_text("[Service]\nExecStart=/vol/apps/worker/run\n")
            (app / "future").write_text("staged only")
            (data / "db.json").write_text(
                json.dumps(
                    {
                        "Services": {
                            "worker": {
                                "ServiceType": "systemd",
                                "ServiceRoot": "/vol/apps/worker",
                                "ServiceRootZFS": "other/worker",
                                "Generation": 1,
                                "LatestGeneration": 2,
                                "Artifacts": {
                                    "systemd.service": {
                                        "Refs": {
                                            "gen-1": "/vol/apps/worker/good",
                                            "latest": "/vol/apps/worker/future",
                                        }
                                    }
                                },
                            }
                        }
                    }
                )
            )
            roots = {"tank/data": data, "other/worker": app}
            disks = {
                "tank/data": ("/srv/catch", 0),
                "other/worker": ("/vol/apps/worker", 0),
            }
            with (
                patch.object(legacy, "datasets", return_value=disks),
                patch.object(
                    legacy,
                    "view",
                    side_effect=lambda ds: contextlib.nullcontext(roots[ds]),
                ),
                patch.object(legacy, "run", return_value=""),
            ):
                saved = legacy.inventory("tank/data", read_units=True)
                row = saved["workloads"][0]
                self.assertTrue(saved["identity"])
                self.assertEqual(
                    row["contents"]["systemd.service"], (app / "good").read_text()
                )
                (app / "good").unlink()
                (app / "good").symlink_to(data / "tsnet/tailscaled.state")
                problems = legacy.inventory("tank/data", read_units=True)["workloads"][
                    0
                ]["problems"]
                self.assertIn("systemd.service", problems[0])

    def test_missing_compose_project_stops_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "containers").mkdir()
            (root / "io.containerd.metadata.v1.bolt").mkdir()
            (root / "io.containerd.metadata.v1.bolt/meta.db").touch()
            disks = {"a/docker": ("/one", 0), "b/containerd": ("/two", 0)}
            with patch.object(
                engine, "view", side_effect=lambda _: contextlib.nullcontext(root)
            ):
                with self.assertRaisesRegex(SetupError, "no saved Compose project"):
                    engine.compose_storage(
                        disks,
                        {"docker": "a/docker", "containerd": "b/containerd"},
                        ["missing"],
                    )

    def test_stage_rejects_vendor_collision_and_creates_only_selected_boot_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vendor = root / "vendor"
            vendor.mkdir()
            units = {
                "job.service": "content",
                "job.timer": "timer",
                "other.service": "other",
            }
            with legacy.stage_units(
                root,
                units,
                {"job": "job.timer", "other": "other.service"},
                ["job"],
                vendor,
                [],
            ) as stage:
                d = Path(stage, "host-systemd/etc/systemd/system")
                self.assertEqual(
                    (d / "timers.target.wants/job.timer").resolve().read_text(), "timer"
                )
                self.assertFalse((d / "multi-user.target.wants").exists())
            (vendor / "job.service").touch()
            with self.assertRaisesRegex(SetupError, "already defines"):
                legacy.stage_units(root, units, {}, [], vendor, [])

    def test_mount_reuse_ignores_a_bind_of_one_file(self):
        from subprocess import CompletedProcess

        result = {
            "filesystems": [
                {"target": "/guest/config.json", "fsroot": "/work/config.json"},
                {"target": "/original", "fsroot": "/"},
            ]
        }
        with (
            patch.object(
                storage.subprocess,
                "run",
                return_value=CompletedProcess([], 0, json.dumps(result)),
            ),
            patch.object(storage, "run") as run,
        ):
            storage.mount_dataset("pool/work", "/review", readonly=True)
            self.assertEqual(
                run.call_args_list[0].args, ("mount", "--bind", "/original", "/review")
            )
