{ config, lib, pkgs, ... }:
let
  cfg = config.services.yeet-host;
  package = pkgs.callPackage ./package.nix { };
  unitDir = "/var/lib/yeet-systemd/etc/systemd/system";
  runtimeTools = with pkgs; [
    package bubblewrap coreutils bash iproute2 iptables ipset nftables procps
    gawk gnugrep gnused dhcpcd util-linux shadow getent docker-compose
    e2fsprogs zstd openssh curl gnutar gzip
  ] ++ lib.optional config.boot.zfs.enabled config.boot.zfs.package
    ++ lib.optionals pkgs.stdenv.hostPlatform.isx86_64 [ pkgs.qemu-utils ];
  # The upstream sandbox masks /run and /nix. Give scripts a self-contained
  # /bin with relative applet links, including bash for bash shebangs.
  sandboxBin = pkgs.runCommand "yeet-sandbox-bin" { } ''
    mkdir -p "$out"
    cp ${pkgs.pkgsStatic.busybox}/bin/busybox "$out/busybox"
    for applet in ${pkgs.pkgsStatic.busybox}/bin/*; do
      name=''${applet##*/}
      [ "$name" = busybox ] || ln -s busybox "$out/$name"
    done
    cp -L --remove-destination ${pkgs.pkgsStatic.bash}/bin/bash "$out/bash"
  '';
in {
  options.services.yeet-host = {
    enable = lib.mkEnableOption "the Yeet OS workload host";
    dataDir = lib.mkOption {
      type = lib.types.strMatching "/[A-Za-z0-9_./-]+";
      default = "/var/lib/yeet";
      description = "Directory where Yeet keeps host data, including its Tailscale identity.";
    };
    servicesRoot = lib.mkOption {
      type = lib.types.strMatching "/[A-Za-z0-9_./-]+";
      default = "${cfg.dataDir}/services";
      description = "Default service root. An exact ZFS mountpoint enables per-service datasets.";
    };
    lanUplink = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Ethernet interface for a DHCP-backed br0 used by VM LAN payloads.";
    };
    hostname = lib.mkOption {
      type = lib.types.nullOr (lib.types.strMatching "[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?");
      default = null;
      description = "The Yeet host’s name on Tailscale. Defaults to the machine hostname.";
    };
  };
  config = lib.mkIf cfg.enable {
    networking.bridges.br0 = lib.mkIf (cfg.lanUplink != null) { interfaces = [ cfg.lanUplink ]; };
    networking.interfaces = lib.optionalAttrs (cfg.lanUplink != null) {
      br0.useDHCP = true;
      ${cfg.lanUplink}.useDHCP = false;
    };
    virtualisation.containerd.enable = true;
    virtualisation.docker = {
      enable = true;
      daemon.settings = {
        containerd = "/run/containerd/containerd.sock";
        # Docker and Yeet own separate firewall tables. A bridge created by
        # Docker cannot displace a verified Yeet isolation rule.
        firewall-backend = "nftables";
        features.containerd-snapshotter = true;
      };
    };
    systemd.services.docker = {
      path = [ pkgs.nftables ];
      after = [ "containerd.service" ];
      requires = [ "containerd.service" ];
    };
    environment.etc."docker/daemon.json".text = builtins.toJSON config.virtualisation.docker.daemon.settings;
    environment.etc."yeet-os/managed".text = "Catch is managed by NixOS.\n";
    environment.etc."yeet-os/sandbox-bin".source = sandboxBin;
    environment.etc."yeet-os/sandbox-lib".source = pkgs.runCommand "yeet-sandbox-lib" {} ''
      mkdir "$out"
      for library in ${pkgs.glibc}/lib/*.so* ${pkgs.stdenv.cc.cc.lib}/lib/lib{gcc_s,stdc++}.so*; do
        cp -L "$library" "$out/"
      done
    '';
    environment.systemPackages = runtimeTools;
    programs.nix-ld.enable = true;
    boot.supportedFilesystems = [ "nfs" "cifs" ];
    # The guest source is routed through yeet0, even while the same packet is
    # crossing its per-VM layer-2 bridge. Strict rpfilter sees both interfaces.
    networking.firewall = lib.mkIf (config.networking.firewall.checkReversePath != false) {
      extraCommands = lib.mkIf (!config.networking.nftables.enable) ''
        iptables -w -t mangle -I nixos-fw-rpfilter 1 -i yvm-+ -s 192.168.100.0/24 -j RETURN
      '';
      extraReversePathFilterRules = lib.mkIf config.networking.nftables.enable ''
        iifname "yvm-*" ip saddr 192.168.100.0/24 accept
      '';
    };
    networking.dhcpcd.denyInterfaces = [ "yeet*" "yi-*" "yo-*" "yv-*" "yvm-*" "veth*" "docker*" "br-*" ];
    boot.kernelModules = [ "tun" "bridge" "veth" "overlay" ];
    boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
    boot.extraSystemdUnitPaths = [ "/etc/systemd/system" unitDir ];
    systemd.settings.Manager.DefaultEnvironment = "PATH=/run/current-system/sw/bin:/usr/bin:/bin";
    users.groups.yeet-svc = {};
    users.users.yeet-svc = {
      isSystemUser = true;
      group = "yeet-svc";
      home = "/nonexistent";
      shell = "/usr/sbin/nologin";
    };
    systemd.tmpfiles.rules = [
      "d ${unitDir} 0755 root root -"
      "d /var/lib/yeet-host/run 0755 root root -"
      "d ${cfg.dataDir} 0711 root root -"
      "d ${cfg.servicesRoot} 0711 root root -"
      "d ${cfg.servicesRoot}/catch/data 0700 root root -"
      "L+ /usr/sbin/nologin - - - - ${pkgs.util-linux}/bin/nologin"
      "L+ /usr/bin/systemd-analyze - - - - ${config.systemd.package}/bin/systemd-analyze"
      "L+ /usr/bin/test - - - - ${pkgs.coreutils}/bin/test"
      "L+ /bin/bash - - - - ${pkgs.bash}/bin/bash"
      "L+ /bin/systemctl - - - - ${config.systemd.package}/bin/systemctl"
      "L+ /sbin/mount.nfs - - - - ${pkgs.nfs-utils}/bin/mount.nfs"
      "L+ /sbin/mount.nfs4 - - - - ${pkgs.nfs-utils}/bin/mount.nfs4"
      "L+ /sbin/mount.cifs - - - - ${lib.getBin pkgs.cifs-utils}/bin/mount.cifs"
      "L+ /bin/rm - - - - ${pkgs.coreutils}/bin/rm"
    ];
    systemd.services.catch = {
      description = "Yeet host service";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" "containerd.service" "systemd-tmpfiles-setup.service" ];
      requires = [ "containerd.service" ];
      environment.SYSTEMD_UNIT_PATH = "/etc/systemd/system:${unitDir}:";
      path = runtimeTools ++ [ config.virtualisation.docker.package config.systemd.package ];
      unitConfig.ConditionPathExists = [
        "|${cfg.dataDir}/tsnet/tailscaled.state"
        "|/run/yeet-enrollment.env"
      ];
      preStart = ''
        # The resolver migration verifies a stable, singly linked executable.
        install -m 0755 ${package}/bin/catch /var/lib/yeet-host/run/.catch.new
        mv -f /var/lib/yeet-host/run/.catch.new /var/lib/yeet-host/run/catch
      '';
      serviceConfig = {
        ExecStart = "/var/lib/yeet-host/run/catch --tsnet-host=${if cfg.hostname == null then "%H" else cfg.hostname} --data-dir=${cfg.dataDir} --services-root=${cfg.servicesRoot}";
        EnvironmentFile = [ "-${cfg.dataDir}/tsnet/enrollment.env" "-/run/yeet-enrollment.env" ];
        WorkingDirectory = "${cfg.servicesRoot}/catch/data";
        Restart = "on-failure";
        RestartSec = 5;
      };
    };
  };
}
