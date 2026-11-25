{ lib, machine, ... }:
let
  net = machine.network;
  static = net.mode == "static";
  mac = net.mac or "";
in {
  # Prepared once per physical machine, shared by the live and installed systems.
  boot.supportedFilesystems = [ "zfs" ];
  boot.zfs.forceImportRoot = false;
  boot.zfs.forceImportAll = false;
  environment.etc."yeet-machine.json".text = builtins.toJSON machine;
  networking = { hostId = lib.mkForce machine.hostId; } // lib.optionalAttrs static {
    useDHCP = lib.mkForce false;
    networkmanager.enable = lib.mkForce false;
    dhcpcd.enable = lib.mkForce false;
    useNetworkd = true;
  };
  systemd.network.networks = lib.optionalAttrs static {
    "10-yeet-uplink" = {
      matchConfig = if mac != "" then { MACAddress = mac; } else { Name = net.interface or "yeet-pending"; };
      address = [ net.address ];
      routes = [ { Gateway = net.gateway; } ];
      dns = net.dns;
      networkConfig.DHCP = "no";
    };
  };
}
