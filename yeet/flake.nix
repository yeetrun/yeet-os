{
  description = "Yeet client and Catch daemon for Yeet OS";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
  outputs = { self, nixpkgs }: {
    overlays.default = import ./overlay.nix;
    apps = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (system:
      nixpkgs.lib.genAttrs [ "catch" "yeet" ] (name: {
        type = "app";
        program = "${self.packages.${system}.default}/bin/${name}";
      }));
    packages = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (system: let
      package = (nixpkgs.legacyPackages.${system}.extend self.overlays.default).yeet;
    in { default = package; yeet = package; catch = package; });
  };
}
