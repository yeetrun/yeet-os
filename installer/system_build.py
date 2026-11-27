"""Build the release with just the facts recorded by the disk wizard."""

import json
import shutil
import tempfile
from pathlib import Path

FACTS = ("host.json", "machine.json", "layout.json", "firmware.json")
SITE_FLAKE = """{
  inputs.os.url = "path:./os";
  outputs = { os, ... }: let
    read = file: builtins.fromJSON (builtins.readFile file);
    base = if builtins.pathExists ./firmware.json then os.lib.installedSite {
      host = read ./host.json;
      machine = read ./machine.json;
      layout = read ./layout.json;
      firmware = read ./firmware.json;
    } else os.nixosConfigurations.${read ./platform.json};
  in {
    nixosConfigurations.yeet-os = base.extendModules {
      modules = if builtins.pathExists ./local/default.nix then [ ./local ] else [];
    };
  };
}
"""


def build_system(
    run, source, nixpkgs, site, system="x86_64-linux", out_link=None, local=None
):
    flags = ["--out-link", str(out_link)] if out_link else ["--no-link"]
    with tempfile.TemporaryDirectory(prefix="yeet-build-") as tmp:
        stage = Path(tmp)
        if site is not None:
            for name in FACTS:
                shutil.copyfile(site / name, stage / name)
        if local is not None and local.exists():
            if not (local / "default.nix").is_file():
                raise ValueError(f"Expected a NixOS module in {local / 'default.nix'}")
            shutil.copytree(local, stage / "local", symlinks=True)
        (stage / "platform.json").write_text(json.dumps(system))
        (stage / "flake.nix").write_text(SITE_FLAKE)
        target = f"path:{stage}#nixosConfigurations.yeet-os"
        overrides = [
            "--override-input",
            "os",
            f"path:{source}",
            "--override-input",
            "os/nixpkgs",
            f"path:{nixpkgs}",
        ]
        return run(
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "build",
            target + ".config.system.build.toplevel",
            *overrides,
            "--no-write-lock-file",
            *flags,
            "--print-out-paths",
        )
