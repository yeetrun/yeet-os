import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installer"))
import provision


class InstallerTests(unittest.TestCase):
    def test_enrollment_uses_a_scoped_token_and_revokes_preflight_keys(self):
        import tailnet
        from urllib.parse import parse_qs

        with tempfile.TemporaryDirectory() as tmp:
            credential = Path(tmp, "secret")
            credential.write_text("tskey-client-example-test")
            enrollment = tailnet.Enrollment(credential)
            responses = [{"access_token": "token"}, {"key": "one-time", "id": "id"}, {}]
            with patch.object(tailnet, "api", side_effect=responses) as api:
                enrollment.preflight(["tag:app"])
        exchange, create, revoke = api.call_args_list
        form = parse_qs(exchange.kwargs["body"].decode())
        self.assertEqual(form["scope"], ["auth_keys"])
        self.assertEqual(form["tags"], ["tag:app"])
        node = json.loads(create.kwargs["body"])["capabilities"]["devices"]["create"]
        self.assertEqual(node, {"tags": ["tag:app"], "reusable": False,
                                "preauthorized": True, "ephemeral": False})
        self.assertEqual(revoke.args, ("tailnet/-/keys/id",))
        self.assertEqual(revoke.kwargs["method"], "DELETE")

    def test_service_tag_cannot_be_a_management_tag(self):
        from tailnet import validate_settings
        settings = {"osTag": "tag:server", "serviceTag": "tag:app", "autoServices": True}
        validate_settings(settings, "minilab", "yeet-minilab")
        for overrides in ({"serviceTag": "tag:catch"}, {"serviceTag": "tag:server"}):
            with self.assertRaises(provision.SetupError):
                validate_settings(settings | overrides, "minilab", "yeet-minilab")

    def test_live_finish_waits_for_enter_before_poweroff(self):
        import setup

        ui = object.__new__(setup.Console)
        ui.page = Mock()
        ui.s = Mock()
        ui.s.getch.side_effect = [setup.curses.KEY_RESIZE, ord("x"), 13]
        with patch.object(setup, "run") as run:
            ui.finish_install("Yeet OS is installed")
        self.assertEqual(ui.s.getch.call_count, 3)
        run.assert_called_once_with("systemctl", "poweroff")

    def test_installed_client_setup_returns_without_powering_off(self):
        import setup

        ui = Mock()
        ui.page.return_value = 8
        with patch.object(setup.curses, "color_pair", return_value=0):
            setup.Console.clients(ui, "yeet-home", False)
        ui.finish_install.assert_not_called()
        self.assertEqual(ui.page.call_args.args[-1], "Enter  Return to shell")

    def test_github_keys_are_reviewed_and_installed_once(self):
        import setup

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keys = []
            for name in ["laptop", "old", "desktop"]:
                path = root / name
                subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
                               check=True)
                keys.append(path.with_suffix(".pub").read_text().strip())
            reply = io.BytesIO(("\n".join(keys + [keys[0]]) + "\n").encode())
            reply.geturl = lambda: "https://github.com/example.keys"
            ui = Mock()
            ui.choose.side_effect = [0, 0, 1, 0]
            ui.field.return_value = "example"
            with patch.object(setup, "urlopen", return_value=reply) as fetch:
                selected = setup.choose_login_keys(ui)
            fetch.assert_called_once_with(reply.geturl(), timeout=10)
            self.assertEqual(selected, [keys[0], keys[2]])
            for key, screen in zip(keys, ui.choose.call_args_list[1:]):
                self.assertIn("@example", screen.args[1])
                self.assertIn(provision.ssh_fingerprint(key), screen.args[2])

            disk = "/dev/lab-target"
            plan = dict(version=1, disk=disk, erase=disk, hostname="lab", ssh_public_keys=selected)
            with patch.object(provision, "disks", return_value=[{"path": disk}]):
                provision.validate(plan)
                with self.assertRaises(provision.SetupError):
                    provision.validate(plan | {"ssh_public_keys": [keys[0], "malformed"]})
                with self.assertRaises(provision.SetupError):
                    provision.validate(plan | {"ssh_public_keys": [keys[0] + "\n" + keys[2]]})
            provision.configure_access(root, plan)
            staged = root / "var/lib/yeet-os/installer-key"
            self.assertEqual(staged.read_text().splitlines(), selected)
            self.assertEqual(staged.stat().st_mode & 0o777, 0o600)
            # Existing prepared profiles and unattended seeds still use a single key.
            provision.configure_access(root, {"ssh_public_key": keys[1]})
            self.assertEqual(staged.read_text(), keys[1] + "\n")

    def test_github_errors_allow_another_sign_in_method(self):
        import setup
        from urllib.error import HTTPError

        url = "https://github.com/example.keys"
        for response, error, detail in [
            (b"", None, "no public SSH keys"),
            (b"not an OpenSSH key", None, "Expected an OpenSSH"),
            (b"x" * (256 * 1024 + 1), None, "too large"),
            (b"", HTTPError(url, 404, "", {}, None), "could not find"),
            (b"", TimeoutError(), "reach GitHub"),
        ]:
            with self.subTest(detail=detail):
                reply = io.BytesIO(response)
                reply.geturl = lambda: url
                ui = Mock()
                ui.field.return_value = "example"
                ui.choose.side_effect = [0, 2]
                with patch.object(setup, "urlopen", return_value=reply, side_effect=error):
                    self.assertEqual(setup.choose_login_keys(ui), [])
                self.assertIn(detail, ui.choose.call_args.args[2])
        with patch.object(setup, "urlopen") as fetch, self.assertRaises(provision.SetupError):
            setup.github_keys("example/another")
        fetch.assert_not_called()

    def test_declining_github_keys_can_fall_back_to_paste(self):
        import setup

        ui = Mock()
        ui.choose.side_effect = [0, 0, 2, 1]
        ui.field.side_effect = ["example", "pasted key"]
        with patch.object(setup, "github_keys", return_value={"fp1": "first", "fp2": "second"}), \
             patch.object(setup, "ssh_fingerprint") as validate:
            self.assertEqual(setup.choose_login_keys(ui), ["pasted key"])
        validate.assert_called_once_with("pasted key")

    def test_key_screen_updates_without_input_after_verification(self):
        import setup

        ui = object.__new__(setup.Console)
        ui.s = Mock()
        ui.s.getmaxyx.return_value = (24, 80)
        ui.s.getch.return_value = -1  # Curses timeout, no operator input.
        ui.page = Mock()
        ui.choose = Mock()
        with patch.object(setup, "installer_ssh_endpoints", return_value=[("192.0.2.10", 2222)]):
            ui.wait_for_zfs_key("tank/apps", Mock(side_effect=[False, False, True]))
        self.assertIn("ssh -T -p 2222 root@192.0.2.10 yeet-zfs-key tank/apps < KEYFILE",
                      ui.page.call_args.args[2])
        self.assertEqual(ui.s.getch.call_count, 2)
        self.assertEqual(ui.choose.call_args.args[1:3], ("Key verified", "tank/apps is unlocked."))
        ui.s.timeout.assert_called_with(-1)

    def test_key_upload_uses_the_address_reached_over_ssh(self):
        import setup

        with patch.object(setup.Path, "exists", return_value=False), patch.dict(
            setup.os.environ, SSH_CONNECTION="2001:db8::2 52000 2001:db8::1 2222"
        ):
            self.assertEqual(setup.installer_ssh_endpoints(), [("2001:db8::1", 2222)])

    def test_prepared_key_upload_uses_public_tailnet_listener(self):
        import setup

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            setup, "Path", side_effect=lambda name: Path(tmp, Path(name).name)
        ), patch.dict(setup.os.environ, SSH_CONNECTION="127.0.0.1 41000 127.0.0.1 2222"):
            Path(tmp, "yeet-machine.json").touch()
            with self.assertRaisesRegex(provision.SetupError, "not ready"):
                setup.installer_ssh_endpoints()
            Path(tmp, "ready").write_text("installer.example.ts.net\n")
            self.assertEqual(setup.installer_ssh_endpoints(), [("installer.example.ts.net", 22)])

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

    def test_vm_lan_uses_a_wired_uplink_with_an_unambiguous_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "wifi" / "wireless").mkdir(parents=True)
            self.assertIsNone(provision.lan_uplink(["wifi"], root))
            self.assertEqual(provision.lan_uplink(["eth1", "wifi"], root), "eth1")
            for routes, expected in [
                (["eth2"], "eth2"),
                (["wifi"], None),
                (["eth1", "eth2"], None),
                ([], None),
            ]:
                with patch.object(provision, "run", return_value=json.dumps([{"dev": r} for r in routes])):
                    self.assertEqual(provision.lan_uplink(["eth1", "eth2", "wifi"], root), expected)

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
            ui.field.return_value = answer
            with self.subTest(answer=answer), patch.object(
                storage_ui, "run", side_effect=[provision.SetupError("previously in use"), ""],
            ) as run:
                result = storage_ui.import_pool(ui, "pool", "42")
                self.assertEqual(result is None, expected)
                self.assertEqual(
                    ("zpool", "import", "-N", "-f", "42") in [c.args for c in run.call_args_list],
                    expected,
                )

    def test_multiselect_import_rescans_partial_results_before_unlock(self):
        import storage_ui

        ui = Mock()
        ui.review_text.side_effect = lambda step, title, lines: "\n".join(lines)
        ui.choose_many.side_effect = [["flash   11", "rust   22"], ["rust   22"]]
        ui.choose.side_effect = [1, 2, 1, 0]  # Confirm, rescan, confirm, unlock.
        with (
            patch.object(storage_ui, "importable", side_effect=[
                [("flash", "11"), ("rust", "22"), ("spare", "33")], [("rust", "22"), ("spare", "33")],
            ]),
            patch.object(storage_ui, "run", side_effect=[
                "", provision.SetupError("device busy"), "flash\t11",
                "", "flash\t11\nrust\t22",
            ]) as run,
            patch.object(storage_ui, "encrypted_recovery") as unlock,
        ):
            storage_ui.import_pools(ui)
        self.assertEqual(
            [c.args for c in run.call_args_list if c.args[1] == "import"],
            [("zpool", "import", "-N", guid) for guid in ("11", "22", "22")],
        )
        first = ui.choose.call_args_list[1].args[2]
        self.assertIn("Imported   flash   11", first)
        self.assertIn("Not imported   rust   22   device busy", first)
        self.assertEqual(ui.choose.call_args.args[2], "Imported   flash   11\nImported   rust   22")
        unlock.assert_called_once_with(ui)

    def test_revisit_reports_imported_pools_without_reimporting(self):
        import storage_ui

        ui = Mock()
        ui.choose.return_value = 1  # Back to storage choices.
        with patch.object(storage_ui, "importable", return_value=[]), patch.object(
            storage_ui, "run", return_value="flash\t11\nrust\t22"
        ) as run:
            storage_ui.import_pools(ui)
        self.assertEqual(ui.review_text.call_args.args[2], ["Imported   flash   11", "Imported   rust   22"])
        ui.choose_many.assert_not_called()
        run.assert_called_once_with("zpool", "list", "-H", "-p", "-o", "name,guid")

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
