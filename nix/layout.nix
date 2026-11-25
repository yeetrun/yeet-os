{ lib, pkgs, config, layout, host, machine ? null, ... }:
let
  locations = builtins.filter (s: s ? dataset) ([ layout.data layout.services ] ++ (layout.extraFilesystems or []));
  data = layout.data.path;
  bridges = layout.bridges or {};
  static = machine != null && machine.network.mode == "static";
  staticBridges = lib.filterAttrs (_: nic: builtins.elem nic host.interfaces) bridges;
  movedStatic = static && staticBridges != {};
  helperBoot = layout.migrationNetwork or false;
  poolOf = dataset: builtins.head (lib.splitString "/" dataset);
  mountedPools = map (loc: poolOf loc.dataset) locations;
  volumePools = lib.unique (map poolOf (layout.vmVolumes or []));
  engines = layout.engineStorage or {};
  legacyEngine = role: builtins.hasAttr role engines;
  engineDir = role: if legacyEngine role then "/var/lib/${role}" else "${data}/host-runtime/${role}";
  engineBinds = lib.mapAttrs' (role: loc: lib.nameValuePair (engineDir role) {
    device = loc.path;
    fsType = "none";
    options = [ "bind" ];
    depends = [ loc.path ];
  }) (lib.filterAttrs (role: loc: loc.path != engineDir role) engines);
in {
  services.yeet-host = { dataDir = data; servicesRoot = layout.services.path; hostname = layout.catchHostname or null; };
  networking.bridges = lib.mapAttrs (_: nic: { interfaces = [ nic ]; }) bridges;
  networking.interfaces = (lib.mapAttrs (name: _: { useDHCP = !static; }) bridges)
    // lib.genAttrs (builtins.attrValues bridges) (_: { useDHCP = false; });
  systemd.network.networks = lib.optionalAttrs movedStatic {
    "10-yeet-uplink".matchConfig = lib.mkForce { Name = builtins.head (builtins.attrNames staticBridges); };
  };
  # The launcher and shared helpers may be needed before Catch refreshes them at boot.
  systemd.tmpfiles.rules = lib.optional helperBoot
    "C /var/lib/yeet-host/run/catch 0755 root root - ${pkgs.yeet-nix}/bin/catch";
  users.users.yeet-svc.uid = lib.mkIf (layout ? serviceUid) layout.serviceUid;
  users.groups.yeet-svc.gid = lib.mkIf (layout ? serviceGid) layout.serviceGid;
  # Keep the engine metadata, named volumes and image layers with the host
  # inventory, so a replacement OS disk can reopen Compose workloads.
  virtualisation.docker.daemon.settings."data-root" = engineDir "docker";
  virtualisation.containerd.settings.root = engineDir "containerd";
  systemd.services.docker.unitConfig.RequiresMountsFor = [ data (engineDir "docker") ] ++ map (loc: loc.path) (layout.extraFilesystems or []);
  systemd.services.containerd.unitConfig.RequiresMountsFor = [ data (engineDir "containerd") ];
  boot.supportedFilesystems = lib.optional (locations != []) "zfs";
  networking.hostId = lib.mkDefault layout.hostId;
  boot.zfs.extraPools = lib.subtractLists mountedPools volumePools;
  boot.zfs.forceImportRoot = false;
  boot.zfs.forceImportAll = false;
  fileSystems = builtins.listToAttrs (map (s: {
    name = s.path;
    value = { device = s.dataset; fsType = "zfs"; neededForBoot = true; options = [ "zfsutil" ]; };
  }) locations) // engineBinds // {
    "/var/lib/yeet-systemd" = {
      device = "${data}/host-systemd";
      fsType = "none";
      options = [ "bind" ];
      neededForBoot = true;
      depends = map (s: s.path) (builtins.filter (s: s.path == data) locations);
    };
  };
  # A shared pool can contain filesystems owned by other machines. Import it
  # without automatically mounting those siblings into this OS.
  systemd.services.zfs-mount.enable = lib.mkIf (locations != []) false;
  systemd.services.yeet-service-datasets = lib.mkIf (layout.services ? dataset) {
    description = "Mount Yeet service datasets";
    requiredBy = [ "local-fs.target" ];
    before = [ "local-fs.target" "systemd-tmpfiles-setup.service" ];
    unitConfig = {
      DefaultDependencies = false;
      RequiresMountsFor = [ layout.services.path ];
    };
    serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
    path = [ config.boot.zfs.package pkgs.util-linux ];
    script = ''
      set -eu -o pipefail
      parent=${lib.escapeShellArg (layout.services.dataset or "")}
      base=${lib.escapeShellArg layout.services.path}
      zfs list -H -r -t filesystem -o name,mountpoint,canmount "$parent" |
        while IFS=$'\t' read -r dataset target canmount; do
          [ "$dataset" != "$parent" ] || continue
          [ "$canmount" = on ] || continue
          case "$target" in "$base"/*) ;; *) echo "Unexpected mountpoint for $dataset" >&2; exit 1;; esac
          if ! findmnt -rn -S "$dataset" -M "$target" >/dev/null; then
            zfs mount "$dataset"
          fi
        done
    '';
  };
  systemd.services.catch = {
    serviceConfig.EnvironmentFile = lib.optional (layout.legacyCatch or false) "-${layout.services.path}/catch/env/env";
    after = lib.optional (layout.services ? dataset) "yeet-service-datasets.service";
    requires = lib.optional (layout.services ? dataset) "yeet-service-datasets.service";
    unitConfig.RequiresMountsFor = [ data layout.services.path "/var/lib/yeet-systemd" ];
  };
}
