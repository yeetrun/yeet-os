import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "installer"))
import zfs_keys as keys
from provision import SetupError


class RecoveryKeys(unittest.TestCase):
    def test_only_roots_required_by_selected_filesystems_and_vm_volumes(self):
        rows = {ds: (root, "available", mode) for ds, root, mode in (
            ("fast/data", "fast", "on"), ("fast/apps", "fast", "on"),
            ("fast/apps/a", "fast/apps/a", "on"), ("fast/apps/off", "fast/apps/off", "off"),
            ("slow/vm", "slow/vm", "-"), ("slow/media", "slow/media", "on"),
            ("slow/other", "slow/other", "on"),
        )}
        layout = dict(data=dict(dataset="fast/data"), services=dict(dataset="fast/apps"),
                      extraFilesystems=[dict(dataset="slow/media")], vmVolumes=["slow/vm"])
        self.assertEqual(keys.required_roots(layout, rows), ["fast", "fast/apps/a", "slow/media", "slow/vm"])
        with self.assertRaisesRegex(SetupError, "missing"):
            keys.required_roots(dict(layout, vmVolumes=["other/missing"]), rows)

    def test_wrong_upload_keeps_verified_copy_and_never_stages_failed_key(self):
        with tempfile.TemporaryDirectory() as d:
            live = Path(d) / "live"
            target = Path(d) / "installed"
            (target / "etc").mkdir(parents=True)
            secret = bytes(range(32))
            def verify(*args):
                self.assertNotIn(secret, args)
                if Path(args[-2].removeprefix("file://")).read_bytes() != secret:
                    raise SetupError("incorrect key")
            with patch.object(keys, "LIVE", live), patch.object(keys, "inventory", return_value={
                "pool": ("pool", "available", "on")
            }), patch.object(keys, "run", side_effect=verify):
                keys.upload("pool", io.BytesIO(secret))
                with self.assertRaises(SetupError):
                    keys.upload("pool", io.BytesIO(b"wrong"))
                with self.assertRaises(SetupError):
                    keys.upload("../etc", io.BytesIO(secret))
                staged = keys.verified_keys(dict(data=dict(dataset="pool"), services={}))
                keys.install_keys(target, staged)
                keyfile = target / keys.INSTALLED / keys.filename("pool")
                self.assertEqual(keyfile.read_bytes(), secret)
                self.assertEqual(keyfile.stat().st_mode & 0o777, 0o400)
                self.assertEqual(keyfile.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(list(live.iterdir()), [live / keyfile.name])
