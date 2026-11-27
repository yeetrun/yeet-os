{ config, lib, pkgs, modulesPath, ... }:
{
  imports = [
    (modulesPath + "/profiles/qemu-guest.nix")
    (modulesPath + "/virtualisation/disk-image.nix")
  ];
  environment.systemPackages = [ (pkgs.callPackage ./setup-tool.nix {}) ];
  services.yeet-host.enable = true;
  networking.hostName = lib.mkDefault "yeet-os";
  services.openssh = {
    enable = true;
    settings = { PasswordAuthentication = false; KbdInteractiveAuthentication = false; };
  };
  services.qemuGuest.enable = true;
  services.cloud-init = {
    enable = true;
    settings = {
      datasource_list = [ "NoCloud" "None" ];
      ssh_deletekeys = false;
      ssh_genkeytypes = [];
    };
  };
  fileSystems."/boot".options = lib.mkIf config.image.efiSupport [ "umask=0077" ];
  boot.kernelParams = [ "console=tty0" "console=ttyS0,115200" ];
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  environment.etc."yeet-os/image".text = "cloud-init\n";
  image.baseName = "yeet-os";
  system.stateVersion = "26.05";
}
