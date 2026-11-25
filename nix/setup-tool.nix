{ writeShellScriptBin, lib, kbd, python3, qrencode, util-linux, coreutils, openssh
, systemd, callPackage, extraInputs ? [], profiles ? {}, osSource ? null, nixpkgsSource ? null }:
let
  yeet = callPackage ./package.nix {};
  path = lib.makeBinPath ([ yeet kbd qrencode util-linux coreutils openssh systemd ] ++ extraInputs);
in writeShellScriptBin "yeet-setup" ''
  export PATH=${path}:$PATH
  ${lib.optionalString (profiles != {}) ''
    export YEET_UEFI_SYSTEM=${profiles.uefi} YEET_BIOS_SYSTEM=${profiles.bios}
    export YEET_OS_SOURCE=${osSource} YEET_NIXPKGS_SOURCE=${nixpkgsSource}
  ''}
  exec ${python3}/bin/python3 -B ${../installer}/setup.py "$@"
''
