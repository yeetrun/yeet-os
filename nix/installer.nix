{ lib, pkgs, modulesPath, profiles, osSource, machine ? null, ... }:
let
  setup = pkgs.callPackage ./setup-tool.nix {
    inherit profiles osSource;
    nixpkgsSource = pkgs.path;
    extraInputs = with pkgs; [
      parted dosfstools e2fsprogs gptfdisk nixos-install-tools nix grub2
      shadow openssl iproute2
    ];
  };
in {
  system.build.yeetSetup = setup;
  imports = [ ./live-access.nix (modulesPath + "/installer/cd-dvd/installation-cd-minimal.nix") ];
  image.baseName = lib.mkForce "yeet-os-installer";
  isoImage.volumeID = "YEET_INSTALL";
  isoImage.squashfsCompression = "zstd -Xcompression-level 3";
  isoImage.storeContents = [ profiles.uefi profiles.bios setup ];
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  boot.kernelParams = [ "console=ttyS0,115200" "console=tty0" ];
  services.openssh.enable = lib.mkForce (machine != null);
  services.qemuGuest.enable = true;
  environment.systemPackages = [ setup ];
  systemd.services.yeet-autoinstall = {
    description = "Install Yeet OS from an explicitly enabled seed";
    wantedBy = [ "multi-user.target" ];
    after = [ "systemd-udev-settle.service" "network-online.target" ];
    wants = [ "systemd-udev-settle.service" "network-online.target" ];
    serviceConfig.Type = "oneshot";
    script = ''
      if test -e /dev/disk/by-label/YEETSEED || grep -qw 'yeet.install=auto' /proc/cmdline; then
        exec ${setup}/bin/yeet-setup --auto
      fi
    '';
  };
  services.getty.autologinUser = lib.mkForce "root";
  programs.bash.interactiveShellInit = lib.mkAfter ''
    if [[ $(id -u) = 0 && -t 0 && -z ''${YEET_INSTALL_SHELL:-} ]]; then
      export YEET_INSTALL_SHELL=1
      if test -e /dev/disk/by-label/YEETSEED || grep -qw 'yeet.install=auto' /proc/cmdline; then
        echo "Unattended install progress: journalctl -fu yeet-autoinstall"
      else
        ${setup}/bin/yeet-setup
      fi
    fi
  '';
}
