{ lib, buildGo127Module }:
buildGo127Module {
  pname = "yeet-nix";
  version = "0.11.16-nix.1";
  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [ ./cmd ./pkg ./go.mod ./go.sum ./README.md ./website/docs ];
  };
  vendorHash = "sha256-lkA5qNE4CVrXK03g+Jbzllj5zE0d5b44kpJ1s6ErhxA=";
  subPackages = [ "cmd/catch" "cmd/yeet" "cmd/yeet-enroll" ];
  env.CGO_ENABLED = 0;
  ldflags = [
    "-s" "-w"
    "-X github.com/yeetrun/yeet/pkg/buildinfo.BuildVersion=v0.11.16-nix.1"
    "-X github.com/yeetrun/yeet/pkg/svc.NixSandboxLauncher=/run/current-system/sw/bin/bwrap"
    "-X github.com/yeetrun/yeet/pkg/svc.SystemdUnitDirectory=/var/lib/yeet-systemd/etc/systemd/system"
    "-X github.com/yeetrun/yeet/pkg/catch.packagedRunner=/var/lib/yeet-host/run/catch"
    "-X github.com/yeetrun/yeet/pkg/catch.bubblewrapPath=/run/current-system/sw/bin/bwrap"
  ];
}
