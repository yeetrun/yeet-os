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
  services.openssh = { enable = true; } // lib.optionalAttrs (machine == null) {
    ports = [ 2222 ];
    openFirewall = true;
    startWhenNeeded = false;
    hostKeys = [ { type = "ed25519"; path = "/etc/ssh/ssh_host_ed25519_key"; } ];
    settings = {
      AllowUsers = [ "root" ];
      AuthenticationMethods = "password";
      PermitRootLogin = "yes";
      PasswordAuthentication = true;
      KbdInteractiveAuthentication = false;
      PermitEmptyPasswords = false;
      DisableForwarding = true;
      ForceCommand = "${setup}/bin/yeet-setup";
    };
  };
  systemd.services.sshd = lib.mkIf (machine == null) {
    requires = [ "yeet-installer-password.service" ];
    after = [ "yeet-installer-password.service" ];
    unitConfig = {
      ConditionPathExists = "!/dev/disk/by-label/YEETSEED";
      ConditionKernelCommandLine = "!yeet.install=auto";
    };
  };
  systemd.services.yeet-installer-password = lib.mkIf (machine == null) {
    after = [ "systemd-udev-settle.service" ];
    wants = [ "systemd-udev-settle.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      RuntimeDirectory = "yeet-installer-login";
      RuntimeDirectoryMode = "0700";
      UMask = "0077";
    };
    script = ''
      set -eu
      directory=/run/yeet-installer-login
      password=$(${pkgs.openssl}/bin/openssl rand -hex 10)
      printf 'root:%s\n' "$password" | ${pkgs.shadow}/bin/chpasswd
      printf '%s\n' "$password" > "$directory/password.tmp"
      mv "$directory/password.tmp" "$directory/password"
    '';
  };
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
  services.getty.helpLine = lib.mkForce "Yeet OS live installer";
  services.getty.autologinUser = lib.mkForce "root";
  programs.bash.interactiveShellInit = lib.mkAfter ''
    if [[ $(id -u) = 0 && -t 0 && -z ''${YEET_INSTALL_SHELL:-} ]]; then
      export YEET_INSTALL_SHELL=1
      if test -e /dev/disk/by-label/YEETSEED || grep -qw 'yeet.install=auto' /proc/cmdline; then
        echo "Unattended install progress: journalctl -fu yeet-autoinstall"
      else
        ${setup}/bin/yeet-setup ${lib.optionalString (machine == null) "--show-login"}
      fi
    fi
  '';
}
