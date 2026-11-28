{ config, host, lib, machine ? null, ... }:
let
  tailnet = host.tailnet or null;
  data = config.services.yeet-host.dataDir;
  uplink = config.services.yeet-host.lanUplink;
  staticBridge = uplink != null && machine != null && machine.network.mode == "static";
  bridges = config.networking.bridges;
  bridgeFor = nic: lib.findFirst (name: builtins.elem nic bridges.${name}.interfaces) nic (builtins.attrNames bridges);
  lan = lib.unique (map bridgeFor host.interfaces ++ lib.optional (uplink != null) "br0");
in {
  services.yeet-host.hostname = lib.mkDefault (host.catchHostname or null);
  fileSystems."/var/lib/tailscale" = lib.mkIf (tailnet != null) {
    device = "${data}/os-tailscale";
    fsType = "none";
    options = [ "bind" "x-systemd.requires-mounts-for=${data}" ];
  };
  services.openssh.hostKeys = lib.mkIf (tailnet != null) [
    { type = "ed25519"; path = "${data}/machine-ssh/ssh_host_ed25519_key"; }
    { type = "rsa"; bits = 4096; path = "${data}/machine-ssh/ssh_host_rsa_key"; }
  ];
  systemd.services.sshd-keygen.unitConfig.RequiresMountsFor = lib.mkIf (tailnet != null) [ data ];
  systemd.services.catch.environment = lib.mkIf (tailnet != null) {
    YEET_SERVICE_TS_TAG = tailnet.serviceTag;
    YEET_AUTO_SERVICE_TS = if tailnet.autoServices then "1" else "0";
  };
  services.yeet-host.lanUplink = lib.mkDefault (host.lanUplink or null);
  networking.interfaces.br0.useDHCP = lib.mkIf staticBridge false;
  systemd.network.networks = lib.optionalAttrs staticBridge {
    "10-yeet-uplink".matchConfig = lib.mkForce { Name = "br0"; };
  };
  networking.useNetworkd = lib.mkIf (bridges != {}) (lib.mkDefault true);
  networking.hostName = lib.mkDefault host.hostname;
  services.avahi.allowInterfaces = if lan == [] then [ "lo" ] else lan;
  networking.firewall.interfaces = lib.genAttrs lan (_: { allowedUDPPorts = [ 5353 ]; });
}
