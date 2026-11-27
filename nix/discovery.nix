{ config, host, lib, machine ? null, ... }:
let
  uplink = config.services.yeet-host.lanUplink;
  staticBridge = uplink != null && machine != null && machine.network.mode == "static";
  bridges = config.networking.bridges;
  bridgeFor = nic: lib.findFirst (name: builtins.elem nic bridges.${name}.interfaces) nic (builtins.attrNames bridges);
  lan = lib.unique (map bridgeFor host.interfaces ++ lib.optional (uplink != null) "br0");
in {
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
