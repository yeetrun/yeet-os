{ config, host, lib, ... }:
let
  uplink = config.services.yeet-host.lanUplink;
  bridges = config.networking.bridges;
  bridgeFor = nic: lib.findFirst (name: builtins.elem nic bridges.${name}.interfaces) nic (builtins.attrNames bridges);
  lan = lib.unique (map bridgeFor host.interfaces ++ lib.optional (uplink != null) "br0");
in {
  networking.hostName = host.hostname;
  services.avahi.allowInterfaces = if lan == [] then [ "lo" ] else lan;
  networking.firewall.interfaces = lib.genAttrs lan (_: { allowedUDPPorts = [ 5353 ]; });
}
