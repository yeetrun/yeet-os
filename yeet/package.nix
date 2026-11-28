{ lib, buildGo127Module }:
buildGo127Module (finalAttrs: {
  pname = "yeet";
  version = lib.trim (builtins.readFile ./VERSION);
  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [ ./cmd ./pkg ./go.mod ./go.sum ./README.md ./website/docs ];
  };
  vendorHash = "sha256-OrvBBLNS+bwSzCD9LZfhMBun7PYITzJZjc9VoyZggCs=";
  subPackages = [ "cmd/catch" "cmd/yeet" "cmd/yeet-enroll" ];
  postInstall = ''
    install -Dm644 cmd/yeet/yeet.bash "$out/share/bash-completion/completions/yeet"
  '';
  env.CGO_ENABLED = 0;
  ldflags = [
    "-s" "-w"
    "-X github.com/yeetrun/yeet/pkg/buildinfo.BuildVersion=v${finalAttrs.version}"
    "-X github.com/yeetrun/yeet/pkg/svc.NixSandboxLauncher=/run/current-system/sw/bin/bwrap"
    "-X github.com/yeetrun/yeet/pkg/svc.SystemdUnitDirectory=/var/lib/yeet-systemd/etc/systemd/system"
    "-X github.com/yeetrun/yeet/pkg/catch.packagedRunner=/var/lib/yeet-host/run/catch"
    "-X github.com/yeetrun/yeet/pkg/catch.bubblewrapPath=/run/current-system/sw/bin/bwrap"
  ];
})
