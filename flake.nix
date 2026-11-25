{
  description = "Yeet OS and its NixOS-native Yeet fork";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      each = nixpkgs.lib.genAttrs systems;
      host = system: nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [ self.nixosModules.default ./nix/image.nix ];
      };
      installed = machine: firmware: nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = { inherit firmware machine; };
        modules = [ self.nixosModules.default ./nix/installed.nix ] ++ nixpkgs.lib.optional (machine != null) ./nix/machine.nix;
      };
      installer = machine: nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        specialArgs = {
          inherit machine;
          osSource = nixpkgs.lib.fileset.toSource {
            root = ./.;
            fileset = nixpkgs.lib.fileset.unions [
              ./flake.nix ./flake.lock ./nix ./installer ./yeet-nix/cmd ./yeet-nix/pkg
              ./yeet-nix/go.mod ./yeet-nix/go.sum ./yeet-nix/README.md ./yeet-nix/website/docs
            ];
          };
          profiles = builtins.mapAttrs (_: system: system.config.system.build.toplevel) {
            uefi = installed machine "uefi";
            bios = installed machine "bios";
          };
        };
        modules = [ ./nix/installer.nix ] ++ nixpkgs.lib.optional (machine != null) ./nix/machine.nix;
      };
    in {
      lib = {
        inherit installed;
        installerISO = machine: (installer machine).config.system.build.isoImage;
      };
      apps = nixpkgs.lib.genAttrs (systems ++ [ "aarch64-darwin" "x86_64-darwin" ]) (system: let
        pkgs = nixpkgs.legacyPackages.${system};
        prepare = pkgs.writeShellScriptBin "yeet-os-prepare" ''
          export PATH=${nixpkgs.lib.makeBinPath [ pkgs.xorriso pkgs.openssh ]}:$PATH
          exec ${pkgs.python3}/bin/python3 -B ${./installer}/prepare.py --source "path:${self.outPath}" "$@"
        '';
      in { prepare = { type = "app"; program = "${prepare}/bin/yeet-os-prepare"; }; });
      nixosModules.default = import ./nix/host.nix;
      nixosConfigurations = each host // { yeet-uefi = installed null "uefi"; yeet-bios = installed null "bios"; };
      packages = each (system: let
        pkgs = nixpkgs.legacyPackages.${system};
      in {
        yeet-nix = pkgs.callPackage ./nix/package.nix { };
        default = self.packages.${system}.yeet-nix;
        image = (host system).config.system.build.image;
      } // nixpkgs.lib.optionalAttrs (system == "x86_64-linux") {
        installer = (installer null).config.system.build.isoImage;
      });
    };
}
