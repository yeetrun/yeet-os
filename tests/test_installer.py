import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installer"))
import provision


class InstallerTests(unittest.TestCase):
    def test_live_console_prefers_ipv4_and_handles_filtered_addresses(self):
        from setup import installer_addresses

        self.assertEqual(
            installer_addresses(
                [
                    {"addr_info": [{}]},
                    {
                        "addr_info": [
                            {
                                "family": "inet6",
                                "scope": "global",
                                "local": "2001:db8::1",
                            },
                            {"family": "inet", "scope": "global", "local": "192.0.2.1"},
                            {},
                        ]
                    },
                    {
                        "addr_info": [
                            {
                                "family": "inet6",
                                "scope": "global",
                                "local": "2001:db8::2",
                            }
                        ]
                    },
                ]
            ),
            ["192.0.2.1", "2001:db8::2"],
        )

    def test_mdns_selects_physical_uplinks_and_matches_static_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, kind, addr, physical in [
                ("ens18", "1", "02:00:00:00:00:01", True),
                ("ens19", "1", "02:00:00:00:00:af", True),
                ("br-test", "1", "", False),
                ("yeet0", "1", "", False),
                ("ib0", "32", "", True),
            ]:
                nic = root / name
                nic.mkdir()
                if physical:
                    (nic / "device").mkdir()
                (nic / "type").write_text(kind)
                (nic / "address").write_text(addr)
            self.assertEqual(
                provision.discovery_interfaces({}, root), ["ens18", "ens19"]
            )
            for selector in [{"interface": "ens19"}, {"mac": "02:00:00:00:00:AF"}]:
                self.assertEqual(
                    provision.discovery_interfaces(selector, root), ["ens19"]
                )
            self.assertEqual(
                provision.discovery_interfaces({"interface": "gone"}, root), []
            )

    def test_discovery_excludes_mounted_children_and_readonly_media(self):
        def device(path, **extra):
            return dict(path=path, type="disk", size=18 * 1024**3, ro=False) | extra

        raw = [
            device(
                "/dev/lab-media",
                children=[{"path": "/dev/lab-p1", "mountpoints": ["/iso"]}],
            ),
            device("/dev/lab-seed", fstype="iso9660"),
            device("/dev/lab-whole-disk-pool", fstype="zfs_member"),
            device("/dev/zd272", fstype="ext4"),
            device("/dev/zd384"),
            device(
                "/dev/lab-partitioned-pool",
                children=[{"path": "/dev/lab-z1", "fstype": "zfs_member"}],
            ),
            device("/dev/lab-readonly", ro=True),
            device("/dev/lab-small", size=1024),
            device("/dev/lab-target"),
        ]
        with patch.object(
            provision, "run", return_value=json.dumps({"blockdevices": raw})
        ) as run:
            self.assertEqual(
                [d["path"] for d in provision.disks()], ["/dev/lab-target"]
            )
            self.assertIn("--tree", run.call_args.args)

    def test_confirmation_and_disk_serial_precede_erasure(self):
        config = dict(
            version=1, disk="/dev/lab-target", hostname="yeet-home", password="local"
        )
        device = dict(path=config["disk"], serial="A")
        with patch.object(provision, "disks", return_value=[device]):
            for changed in [
                dict(erase="/dev/other"),
                dict(erase=device["path"], serial="B"),
            ]:
                with self.assertRaises(provision.SetupError):
                    provision.validate(config | changed)
            self.assertEqual(
                provision.validate(config | dict(erase=device["path"], serial="A")),
                device,
            )

    def test_failed_pairing_preserves_existing_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "var/lib/yeet/tsnet"
            state.mkdir(parents=True)
            (state / "identity").write_text("old")
            with patch.object(
                provision, "enroll_attempt", side_effect=provision.SetupError("offline")
            ):
                with self.assertRaises(provision.SetupError):
                    provision.enroll(root, "yeet-home", None, lambda *_: None)
            self.assertEqual((state / "identity").read_text(), "old")

    def test_key_fallback_publishes_only_completed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def attempt(state, hostname, key, _):
                state.mkdir()
                (state / "identity").write_text("ready" if key else "incomplete")
                return (hostname, None) if key else (None, Path("/key-file"))

            with patch.object(provision, "enroll_attempt", side_effect=attempt):
                self.assertEqual(
                    provision.enroll(root, "yeet-home", None, lambda *_: None),
                    "yeet-home",
                )
            dest = root / "var/lib/yeet/tsnet"
            self.assertEqual((dest / "identity").read_text(), "ready")
            self.assertEqual(
                (dest / "enrollment.env").read_text().strip(),
                "CATCH_TSNET_ADVERTISE_TAG=tag:catch",
            )
            self.assertFalse(list(dest.parent.glob(".enroll-*")))


class StorageAndProfileTests(unittest.TestCase):
    def test_restore_selects_the_path_from_the_data_on_disk(self):
        import contextlib
        import storage
        import storage_ui
        from unittest.mock import Mock

        ds = "tank/yeet"
        record = {"hostname": "home", "layout": {"data": {"dataset": ds}}}
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(
                storage,
                "view",
                side_effect=lambda _: contextlib.nullcontext(Path(tmp).resolve()),
            ),
            patch.object(storage_ui, "datasets", return_value={ds: ("/tank/yeet", 1)}),
            patch("legacy.review", return_value=(ds, record)) as discover,
        ):
            ui = Mock()
            ui.choose.side_effect = [1, 0]
            self.assertEqual(storage_ui.recover(ui), (ds, record))
            discover.assert_called_once_with(ui, ds)
            discover.reset_mock()

            # Damaged settings must raise an error, even when the app list is present.
            root = Path(tmp)
            (root / "db.json").write_text('{"Services": {}}')
            saved = root / "yeet-os-layout.json"
            saved.write_text("broken")
            ui.choose.side_effect = [1, 0]
            with self.assertRaisesRegex(provision.SetupError, "saved settings file"):
                storage_ui.recover(ui)
            discover.assert_not_called()

            record["version"] = 1
            record["layout"]["data"]["path"] = "/tank/yeet"
            record["layout"]["services"] = {"path": "/tank/yeet/services"}
            saved.write_text(json.dumps(record))
            (root / "host-systemd/etc/systemd/system").mkdir(parents=True)
            (root / "tsnet").mkdir()
            (root / "tsnet/tailscaled.state").touch()
            ui.choose.side_effect = [1, 0, 1]
            with (
                patch.object(storage, "validate_layout"),
                patch.object(storage, "datasets", return_value={}),
            ):
                dataset, restored = storage_ui.recover(ui)
            self.assertEqual((dataset, restored["hostname"]), (ds, "home"))
            discover.assert_not_called()

    def test_refuse_nonempty_or_occupied_dataset_on_fresh_install(self):
        import contextlib
        import storage

        layout = {
            "data": {"dataset": "tank/data", "path": "/tank/data"},
            "services": {"path": "/tank/data/services"},
            "hostId": "cafeface",
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(
                storage, "datasets", return_value={"tank/data": ("/tank/data", 100)}
            ),
            patch.object(storage, "run", return_value="tank/data"),
            patch.object(
                storage, "view", return_value=contextlib.nullcontext(Path(tmp))
            ),
        ):
            (Path(tmp) / "old-host").write_text("precious")
            with self.assertRaisesRegex(provision.SetupError, "contains data"):
                storage.validate_layout(layout)
            layout["data"]["create"] = True
            with self.assertRaisesRegex(provision.SetupError, "already exists"):
                storage.validate_layout(layout)

    def test_service_root_requires_dedicated_location_or_default_subdirectory(self):
        import storage

        for path in [
            "/",
            "/var",
            "/nix/store/whatever",
            "/srv/../etc",
            "/var/lib/yeet-systemd/units",
        ]:
            with self.subTest(path=path), self.assertRaises(provision.SetupError):
                storage.data_path(path)
        with self.assertRaises(provision.SetupError):
            storage.validate_layout(
                {
                    "data": {"path": "/srv/yeet"},
                    "services": {"path": "/srv/yeet/custom"},
                    "hostId": "cafeface",
                }
            )

    def test_generic_static_install_saves_network_and_preserves_recovered_host_id(self):
        network = dict(
            mode="static",
            interface="ens18",
            address="192.0.2.8/24",
            gateway="192.0.2.1",
            dns=["192.0.2.1"],
        )
        config = dict(
            hostname="apps",
            password="console-only",
            network=network,
            layout={"hostId": "abcdef12"},
            recover_dataset="pool/apps",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            site = Path(tmp) / "site"
            with (
                patch.dict(
                    provision.os.environ,
                    YEET_OS_SOURCE=str(source),
                    YEET_NIXPKGS_SOURCE=tmp,
                ),
                patch.object(provision, "run"),
                patch.object(provision, "machine_profile", return_value=None),
                patch.object(provision, "discovery_interfaces", return_value=["ens18"]),
            ):
                provision.prepare_system(site, config, "uefi")
            self.assertEqual(
                json.loads((site / "machine.json").read_text()),
                {
                    "hostname": "apps",
                    "network": network,
                    "hostId": "abcdef12",
                    "sshPublicKey": "",
                },
            )

    def test_mutable_state_separate_from_immutable_profile(self):
        import prepare

        m = {
            "hostname": "homebox",
            "hostId": "c0ffee01",
            "sshPublicKey": "ssh-ed25519 test",
            "network": {
                "mode": "static",
                "mac": "02:00:00:00:00:01",
                "address": "192.168.1.12/24",
                "gateway": "192.168.1.1",
                "dns": ["1.1.1.1"],
            },
        }
        with patch.object(prepare, "run"):
            prepare.validate_machine(m)
            import copy

            for path, value in [
                ("mac", "*"),
                ("address", "192.168.1.12"),
                ("gateway", "not.an.address"),
                ("dns", []),
            ]:
                bad = copy.deepcopy(m)
                bad["network"][path] = value
                with self.subTest(path=path), self.assertRaises(ValueError):
                    prepare.validate_machine(bad)
            with self.assertRaisesRegex(ValueError, "public profile"):
                prepare.validate_machine(m | {"authKey": "secret"})

    def test_export_only_selected_idle_pools(self):
        import storage

        layout = {
            "data": {"dataset": "one/host"},
            "services": {"dataset": "two/services"},
        }
        with patch.object(storage, "run", side_effect=["no\nno", "", "no\nyes"]) as run:
            storage.release_idle_pools(layout)
        self.assertIn(("zpool", "export", "one"), [c.args for c in run.call_args_list])
        self.assertNotIn(
            ("zpool", "export", "two"), [c.args for c in run.call_args_list]
        )

    def test_takeover_requires_exact_pool_confirmation(self):
        import storage_ui
        from unittest.mock import Mock

        for answer, expected in [("pool", True), ("", False), ("other", False)]:
            ui = Mock()
            ui.choose.side_effect = [1, 0, 1]
            ui.field.return_value = answer
            with (
                self.subTest(answer=answer),
                patch.object(storage_ui.shutil, "which", return_value="zpool"),
                patch.object(storage_ui, "importable", return_value=[("pool", "42")]),
                patch.object(
                    storage_ui,
                    "run",
                    side_effect=[provision.SetupError("previously in use"), ""],
                ) as run,
            ):
                if answer == "other":
                    with self.assertRaises(provision.SetupError):
                        storage_ui.pool_tools(ui)
                else:
                    storage_ui.pool_tools(ui)
                self.assertEqual(
                    ("zpool", "import", "-N", "-f", "42")
                    in [c.args for c in run.call_args_list],
                    expected,
                )

    def test_detect_external_workload_roots_before_recovery(self):
        import storage

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(storage, "datasets", return_value={}),
        ):
            root = Path(tmp)
            (root / "db.json").write_text(
                json.dumps(
                    {"Services": {"worker": {"ServiceRoot": "/other-disk/worker"}}}
                )
            )
            with self.assertRaisesRegex(provision.SetupError, "external root"):
                storage.check_saved_services(
                    root, {"services": {"path": "/tank/services"}}
                )


if __name__ == "__main__":
    unittest.main()
