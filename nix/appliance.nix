{ lib, pkgs, releaseSources, ... }:
let
  version = lib.trim (builtins.readFile ../VERSION);
  hostLogin = pkgs.writeShellScript "yeet-host-login" ''
    if [ "$#" -eq 0 ]; then
      exec ${pkgs.util-linux}/bin/runuser --login yeet
    fi
    # Start with the login profile and pass the command as literal argv.
    exec ${pkgs.util-linux}/bin/runuser --login --shell ${pkgs.bash}/bin/bash \
      --command 'exec "$@"' -- yeet yeet-ssh "$@"
  '';
  installLoginKey = pkgs.writeShellScript "install-login-key" ''
    set -eu
    umask 077
    cd "$HOME"
    mkdir -p .ssh
    tmp=$(mktemp .ssh/.authorized_keys.XXXXXXXX)
    trap 'rm -f "$tmp"' EXIT
    cat > "$tmp"
    mv -T "$1" "$tmp" .ssh/authorized_keys
  '';
  homeConfig = pkgs.writeText "home.nix" ''
    { pkgs, ... }: {
      home.username = builtins.getEnv "USER";
      home.homeDirectory = builtins.getEnv "HOME";
      home.stateVersion = "26.05";

      home.packages = [
        # pkgs.ripgrep
      ];
    }
  '';
in {
  users.users.yeet = {
    isNormalUser = true;
    description = "Yeet administrator";
    extraGroups = [ "wheel" ];
  };
  security.sudo.extraRules = [{
    users = [ "yeet" ];
    commands = [{ command = "ALL"; options = [ "NOPASSWD" ]; }];
  }];
  services.openssh.settings.PermitRootLogin = lib.mkDefault "no";
  systemd.services.catch.environment.YEET_HOST_LOGIN = toString hostLogin;
  systemd.sockets.catch.socketConfig = {
    DirectoryMode = "0711";
    SocketUser = "yeet";
  };

  # Install the wizard key, or import a legacy key file once before SSH starts.
  # A key removed from the new account must stay revoked across reboots.
  systemd.services.yeet-login-migration = {
    before = [ "sshd.service" "sshd.socket" ];
    requiredBy = [ "sshd.service" ];
    wantedBy = [ "multi-user.target" ];
    unitConfig.ConditionPathExists = "!/var/lib/yeet-os/login-migrated";
    serviceConfig = { Type = "oneshot"; StateDirectory = "yeet-os"; };
    script = ''
      key=/var/lib/yeet-os/installer-key
      if test -s "$key"; then
        ${pkgs.util-linux}/bin/runuser -u yeet -- ${installLoginKey} --force < "$key"
        rm "$key"
      elif test -s /root/.ssh/authorized_keys; then
        ${pkgs.util-linux}/bin/runuser -u yeet -- ${installLoginKey} --update=none < /root/.ssh/authorized_keys
      fi
      touch /var/lib/yeet-os/login-migrated
    '';
  };
  environment.systemPackages = with pkgs; [
    (callPackage ./update-tool.nix {}) home-manager
    # Processes, open files and performance
    htop btop sysstat iotop lsof strace
    # Network troubleshooting
    dnsutils ethtool iftop iperf3 mtr netcat-openbsd tcpdump traceroute
    # Hardware and storage
    duf ioping lm_sensors ncdu nvme-cli parted pciutils smartmontools usbutils
    # Interactive administration and file transfers
    file gitMinimal jq lnav ripgrep rsync tmux tree unzip vim wget zip
  ];
  system.systemBuilderCommands = ''
    ln -s ${releaseSources.source} "$out/yeet-os-source"
    ln -s ${releaseSources.nixpkgs} "$out/yeet-os-nixpkgs"
  '';
  environment.etc."yeet-os/version".text = version + "\n";
  system.nixos.label = "yeet-os-${version}";
  nix.nixPath = [ "nixpkgs=${releaseSources.nixpkgs}" "home-manager=${pkgs.home-manager.src}" ];
  # Seed the installer's login account once. Its edited config belongs to the user.
  systemd.tmpfiles.rules = [
    # Update the existing socket and directory during a live upgrade as well.
    "d /run/yeet-admin 0711 root root -"
    "z /run/yeet-admin/rpc.sock 0600 yeet root -"
    "C /etc/nixos/local/default.nix 0644 root root - ${./local-default.nix}"
    "d /home/yeet/.config 0700 yeet users -"
    "d /home/yeet/.config/home-manager 0700 yeet users -"
    "C /home/yeet/.config/home-manager/home.nix 0600 yeet users - ${homeConfig}"
  ];
  environment.etc."skel/.config/home-manager/home.nix" = { source = homeConfig; mode = "0644"; };
  environment.loginShellInit = ''
    for profile in "''${XDG_STATE_HOME:-$HOME/.local/state}/nix/profiles/profile" "$HOME/.nix-profile"; do
      if [ -r "$profile/etc/profile.d/hm-session-vars.sh" ]; then
        . "$profile/etc/profile.d/hm-session-vars.sh"
        break
      fi
    done
    unset profile
  '';
}
