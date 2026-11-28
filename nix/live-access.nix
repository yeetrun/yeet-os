{ lib, pkgs, machine, config, ... }:
lib.mkIf (machine != null) {
  networking.hostName = lib.mkForce machine.hostname;
  services.openssh = {
    openFirewall = false;
    listenAddresses = [ { addr = "127.0.0.1"; port = 2222; } ];
    settings = { PasswordAuthentication = false; KbdInteractiveAuthentication = false; PermitRootLogin = "prohibit-password"; };
    extraConfig = ''
      DisableForwarding yes
      ForceCommand ${config.system.build.yeetSetup}/bin/yeet-setup
    '';
  };
  users.users.root.openssh.authorizedKeys.keys = [ machine.sshPublicKey ];
  systemd.services.yeet-select-uplink = lib.mkIf
    (machine.network.mode == "static" && (machine.network.mac or "") == "" && (machine.network.interface or "") == "") {
    wantedBy = [ "multi-user.target" ];
    before = [ "network-online.target" ];
    wants = [ "systemd-udev-settle.service" "systemd-networkd.service" ];
    after = [ "systemd-udev-settle.service" "systemd-networkd.service" ];
    serviceConfig = { Type = "oneshot"; ExecStart = "${config.system.build.yeetSetup}/bin/yeet-setup --select-uplink"; };
  };
  systemd.services.yeet-live-access = {
    wantedBy = [ "multi-user.target" ];
    wants = [ "network-online.target" "sshd.service" ];
    after = [ "network-online.target" "sshd.service" ];
    unitConfig.ConditionPathExists = "/iso/yeet-private/catch.key";
    serviceConfig = {
      Restart = "on-failure";
      RestartSec = "5s";
      RuntimeDirectory = "yeet-live";
      RuntimeDirectoryMode = "0700";
      RuntimeDirectoryPreserve = "restart";
      ExecStart = "${pkgs.yeet}/bin/yeet-enroll --installer-ssh --hostname=${machine.hostname} --state=/run/yeet-live/tsnet --key-file=/iso/yeet-private/catch.key";
    };
  };
}
