{ lib, pkgs, releaseSources, ... }:
let
  version = lib.trim (builtins.readFile ../VERSION);
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
  environment.systemPackages = [ (pkgs.callPackage ./update-tool.nix {}) pkgs.home-manager ];
  system.systemBuilderCommands = ''
    ln -s ${releaseSources.source} "$out/yeet-os-source"
    ln -s ${releaseSources.nixpkgs} "$out/yeet-os-nixpkgs"
  '';
  environment.etc."yeet-os/version".text = version + "\n";
  system.nixos.label = "yeet-os-${version}";
  nix.nixPath = [ "nixpkgs=${releaseSources.nixpkgs}" "home-manager=${pkgs.home-manager.src}" ];
  # Seed the installer's login account once. Its edited config belongs to the user.
  systemd.tmpfiles.rules = [
    "C /etc/nixos/local/default.nix 0644 root root - ${./local-default.nix}"
    "C /root/.config/home-manager/home.nix 0600 root root - ${homeConfig}"
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
