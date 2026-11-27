{
  description = "Yeet OS and its NixOS-native Yeet fork";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      each = nixpkgs.lib.genAttrs systems;
      yeetOverlay = import ./yeet-nix/overlay.nix;
      packagesModule.nixpkgs.overlays = [ yeetOverlay ];
      osSource = nixpkgs.lib.fileset.toSource {
        root = ./.;
        fileset = nixpkgs.lib.fileset.unions [
          ./VERSION ./flake.nix ./flake.lock ./.tack/pins.toml ./.tack/pins.lock.json
          ./nix ./installer ./yeet-nix/cmd ./yeet-nix/pkg
          ./yeet-nix/flake.nix ./yeet-nix/flake.lock ./yeet-nix/overlay.nix ./yeet-nix/package.nix
          ./yeet-nix/go.mod ./yeet-nix/go.sum ./yeet-nix/README.md ./yeet-nix/website/docs
        ];
      };
      appliance = {
        imports = [ ./nix/appliance.nix ];
        _module.args.releaseSources = self.lib.updateSources;
      };
      host = system: nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [ self.nixosModules.default ./nix/image.nix appliance ];
      };
      installed = machine: firmware: nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = { inherit firmware machine; };
        modules = [ self.nixosModules.default ./nix/installed.nix appliance ] ++ nixpkgs.lib.optional (machine != null) ./nix/machine.nix;
      };
      installer = machine: nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = {
          inherit machine;
          inherit osSource;
          profiles = builtins.mapAttrs (_: system: system.config.system.build.toplevel) {
            uefi = installed machine "uefi";
            bios = installed machine "bios";
          };
        };
        modules = [ packagesModule ./nix/installer.nix ] ++ nixpkgs.lib.optional (machine != null) ./nix/machine.nix;
      };
    in {
      lib = {
        inherit installed;
        updateSources = { source = toString osSource; nixpkgs = nixpkgs.outPath; };
        installedSite = { machine, firmware, layout, host }:
          (installed machine firmware).extendModules {
            specialArgs = { inherit layout host; };
            modules = [ ./nix/discovery.nix ] ++ nixpkgs.lib.optional (layout != null) ./nix/layout.nix;
          };
        installerISO = machine: (installer machine).config.system.build.isoImage;
      };
      apps = nixpkgs.lib.genAttrs (systems ++ [ "aarch64-darwin" "x86_64-darwin" ]) (system: let
        pkgs = nixpkgs.legacyPackages.${system};
        prepare = pkgs.writeShellScriptBin "yeet-os-prepare" ''
          export PATH=${nixpkgs.lib.makeBinPath [ pkgs.xorriso pkgs.openssh ]}:$PATH
          exec ${pkgs.python3}/bin/python3 -B ${./installer}/prepare.py --source "path:${self.outPath}" "$@"
        '';
      in { prepare = { type = "app"; program = "${prepare}/bin/yeet-os-prepare"; }; });
      nixosModules.default.imports = [ packagesModule ./nix/host.nix ];
      nixosConfigurations = each host // { yeet-uefi = installed null "uefi"; yeet-bios = installed null "bios"; };
      packages = each (system: {
        yeet-nix = (nixpkgs.legacyPackages.${system}.extend yeetOverlay).yeet-nix;
        yeet-os = nixpkgs.legacyPackages.${system}.callPackage ./nix/update-tool.nix {};
        default = self.packages.${system}.yeet-nix;
        image = (host system).config.system.build.image;
      } // nixpkgs.lib.optionalAttrs (system == "x86_64-linux") {
        installer = (installer null).config.system.build.isoImage;
      });
    };
}
