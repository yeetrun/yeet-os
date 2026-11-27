# Running a Yeet OS host

## Build and boot

On an x86-64 Linux Nix builder with KVM, run the following from the repository root.

```sh
nix --extra-experimental-features 'nix-command flakes' build .#image
ls -lh result/yeet-os.qcow2
```

The output is a UEFI QCOW2 disk. The flake also exports the Yeet client and host binaries as `packages.<system>.yeet-nix` for x86-64 and ARM64 Linux. Firecracker VM payloads require x86-64 and access to KVM and TUN in the host.

On Proxmox, copy the QCOW2 to the hypervisor, then run the following as root. Choose an unused VM ID, a disk storage, the LAN bridge, and the public key you will use to sign in. The example provisions 6 GiB of RAM and a 40 GiB workload disk. Size both for the payloads you intend to run.

```bash
vmid=$(pvesh get /cluster/nextid)
storage=local-lvm
image=/var/tmp/yeet-os.qcow2
pubkey=/root/.ssh/id_ed25519.pub
qm create "$vmid" --name yeet-os --machine q35 --bios ovmf --cpu host \
  --cores 4 --memory 6144 --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-pci \
  --agent enabled=1 --serial0 socket
qm set "$vmid" --efidisk0 "$storage:1,efitype=4m,pre-enrolled-keys=0" \
  --scsi0 "$storage:0,import-from=$image" --ide2 "$storage:cloudinit" \
  --ciuser yeet --sshkeys "$pubkey" --ipconfig0 ip=dhcp --boot order=scsi0
qm resize "$vmid" scsi0 40G
qm start "$vmid"
qm guest cmd "$vmid" network-get-interfaces
```

Wait for the guest agent, then connect with `ssh yeet@<guest-ip>`. The image accepts a NoCloud public key and grows its root filesystem on boot.

## Connect the host to Tailscale and deploy an app

Use a Tailscale auth key with the `tag:catch` tag for the host. For a fleet, choose a unique `services.yeet-host.hostname` in each machine's configuration before enrollment. The generic image uses `yeet-os`. Run `sudo -s` inside the guest, then run these commands in Bash. The enrollment file lives in tmpfs.

```bash
catch host-check
umask 077
read -rsp 'Tailscale auth key: ' key && echo
printf 'TS_AUTHKEY=%s\n' "$key" > /run/yeet-enrollment.env
unset key
systemctl start catch
journalctl -u catch -n 30
test -s /var/lib/yeet/tsnet/tailscaled.state && rm -f /run/yeet-enrollment.env
```

The existing Yeet tailnet grants apply. A host auth key connects the Yeet host to Tailscale. Workloads requesting their own Tailscale identity also need an authorized service enrollment credential. Yeet accepts `TS_CLIENT_SECRET` in the same enrollment file and persists it for service provisioning. Keep credentials outside the flake and VM image. [Tailnet permissions and service tags](../yeet-nix/website/docs/concepts/tailscale.mdx)

On your workstation, use the existing Yeet client and your usual service workspace. To build the fork's client locally, run `cd yeet-nix && mise exec -- go build -o ./yeet ./cmd/yeet` and invoke `./yeet` from that directory.

```bash
export CATCH_HOST=yeet-os
yeet version
yeet run web nginx:alpine -p 8080:80
yeet ssh -- curl -fsS http://127.0.0.1:8080/
yeet run worker ./worker.sh
yeet run nightly ./backup.sh --cron='0 2 * * *'
yeet run devbox vm://nixos/26.05
yeet ssh devbox -- uname -a
```

The installed host also includes the CLI. Sign in over SSH as `yeet` and run `yeet status` or `yeet restart web`. From a workstation, `yeet ssh` starts a login shell as `yeet` on this host. On Yeet OS, `localhost` selects the Unix socket at `/run/yeet-admin/rpc.sock`. Starting with v0.3.5, the `yeet` account can administer local workloads through this socket without sudo. You can run these commands as soon as SSH accepts your login after a reboot. Systemd queues the connection while Yeet starts. Explicit host settings and project targets still apply. Use `yeet restart web@localhost` to select this machine explicitly.

## Build the Yeet binaries

From a Linux checkout, run `nix build ./yeet-nix#catch`. The output includes `result/bin/catch`, `result/bin/yeet`, and `result/bin/yeet-enroll`. The OS imports the fork’s package overlay and builds these same binaries with its pinned nixpkgs.

## Update an installed host

Sign in as `yeet`. Run `sudo -s` for an administrative shell, or prefix individual administrative commands with `sudo`. `yeet-os check` shows the running, selected and available releases. `sudo yeet-os upgrade` activates the latest stable release and selects it for future boots. Your current SSH shell can use newly installed programs immediately. NixOS starts enabled system services and reloads or restarts affected units. Use `sudo yeet-os upgrade --boot-only` to defer activation to a maintenance window.

`yeet-os status` works offline. After a live switch, it compares the next boot's kernel, module tree, initrd (which can include early CPU microcode), and kernel parameters with the booted generation. If they differ, reboot when ready to use the new boot components. A change that NixOS marks incompatible with live switching, such as switching D-Bus implementations, stages the entire generation and reports why. Yeet OS never reboots the host automatically.

The updater installed in v0.3.2 and earlier always stages the new generation. To activate an already staged release immediately on one of those hosts, run `sudo nixos-rebuild switch --no-reexec --store-path "$(readlink -f /nix/var/nix/profiles/system)"`. If NixOS declines the switch, reboot into the staged generation.

The installer saves four machine-specific files under `/etc/nixos`. They describe the boot mode, networking, hostname and storage chosen in the wizard. Yeet supplies the NixOS modules, pinned nixpkgs and Yeet binaries with each release. The host imports those modules, applies its own settings and builds a complete NixOS generation. New upstream packages are fetched from the Nix binary cache. Each retained generation keeps its release sources so you can rebuild with the same pin.

### Change settings on one machine

Edit `/etc/nixos/local/default.nix` as root. This is a regular NixOS module and can import other files in the same directory. For example:

```nix
{ pkgs, ... }: {
  networking.hostName = "workshop";
  environment.systemPackages = [ pkgs.tmux pkgs.ripgrep ];
  fileSystems."/srv/archive" = {
    device = "/dev/disk/by-label/ARCHIVE";
    fsType = "ext4";
    options = [ "nofail" ];
  };
}
```

Use the device and filesystem type from your machine. Run `sudo yeet-os rebuild` to apply your edits using the selected release. Use `--boot-only` to defer them. The next `yeet-os upgrade` includes the same local module. Ordinary NixOS option priorities apply. Some disk or network options that already have a value from the installer may need `lib.mkForce` to replace it. Keep secrets outside the module directory because Nix copies its contents into the readable Nix store.

### Customize your login

Sign in as `yeet` and edit `~/.config/home-manager/home.nix`. Run Home Manager as that user. Add packages to `home.packages`, or set a variable such as `home.sessionVariables.EDITOR = "nano";`. Run `home-manager switch` and sign in again. The Home Manager CLI and its nixpkgs come from the active OS release. Your file and personal profile stay in place across OS upgrades. Back up both your home directory and `/etc/nixos/local` if you plan to replace the OS disk.

### Roll back

Run `yeet-os rollback` to select the previous system generation, then reboot. If SSH is unavailable, choose a prior generation in the boot menu. A rollback selects the OS and host settings built into that generation. Your editable files stay on disk for the next rebuild. Manage app releases separately with `yeet service rollback`.

### Moving from the root login

On v0.3.0 and earlier, the default SSH login was `root`. On the first boot of a release with the `yeet` account, the host copies the old installer key file from `/root/.ssh/authorized_keys` if `yeet` has no key file yet. Sign in with the same key as `yeet`. If you manage keys in a local NixOS module, set `users.users.yeet.openssh.authorizedKeys.keys` there before upgrading. Your existing root home and Home Manager configuration stay in `/root`. Copy any personal settings you want to use into the new account. For console access on an upgraded host, set a password with `sudo passwd yeet`.

### Upgrade from v0.2.2

The v0.2.2 ISO predates the updater. On a stock ISO-installed host, run the v0.3.0 CLI once as root:

```sh
nix run 'https://github.com/yeetrun/yeet-os/archive/refs/tags/v0.3.0.tar.gz#yeet-os' -- upgrade
reboot
```

The updater reads the existing installer settings and records the boot mode from the old site flake. Your original site flake and source copy remain on disk. If you hand-edited either of them, move those overrides into `/etc/nixos/local/default.nix` before the first upgrade. Later upgrades use the installed `yeet-os` command.

## Build update assets for a release

Set `VERSION` to the intended stable version before tagging the source. On a native Linux builder, use a clean checkout of that tag. Run the following with Python 3.11+, Nix and zstd:

```sh
tools/build-update /tmp/yeet-release-assets
```

Run once per supported architecture. Upload all four files from each output directory alongside the ISO and checksum to the **draft** GitHub Release tagged `v<VERSION>`. Publish it as latest after the assets are present. Keep published assets immutable.

Hosts read a small JSON file at `releases/latest/download/yeet-os-<system>.json` over HTTPS. Its version selects a tag-specific URL for each missing asset, so concurrent releases cannot mix downloads. Each manifest names the exact Nix store paths and SHA-256 digests of the compressed exports. Hosts verify downloaded exports and reuse paths already in the store. The trust anchor for the manifest is the project's GitHub release over HTTPS. For a staged rollout, copy the manifest and assets to a directory on a test host and run `yeet-os upgrade --manifest /path/to/yeet-os-x86_64-linux.json`.

## Update nixpkgs for a release

Yeet OS and the standalone Yeet flake share the nixpkgs pin in `.tack/`. From the repository root, use Tack 2, Nix and Python 3.11 or newer to review and apply a branch update.

```sh
tack look nixpkgs
tools/update-nixpkgs
git diff -- .tack/pins.lock.json flake.lock yeet-nix/flake.lock
```

The release builder checks both flake locks against Tack before exporting assets. On a running host, `/run/current-system/yeet-os-source/.tack/pins.lock.json` records the upstream nixpkgs revision for that generation. The Yeet package and NixOS modules come from the same Yeet OS release.

If Tack is missing, run `nix shell github:manic-systems/tack/v2.0.0 --command tools/update-nixpkgs`.

Run `tools/update-nixpkgs --check` to check both locks offline. The check uses Nix and Python and also runs in pre-commit when the pin or either flake changes. Builds and installs use the checked-in flake locks. To switch release branches, change the URL in `.tack/pins.toml` and both `flake.nix` declarations before running the update command. [Tack installation](https://github.com/manic-systems/tack/blob/v2.0.0/docs/obtaining.md)

## Use the module in your own NixOS distribution

Import `nixosModules.default` into an existing NixOS flake and enable `services.yeet-host.enable`. To customize the distributed disk, extend the image configuration. For example, in a site flake next to this checkout:

```nix
{
  inputs.yeet-os.url = "path:../yeet-os";
  outputs = { yeet-os, ... }: {
    nixosConfigurations.apps = yeet-os.nixosConfigurations.x86_64-linux.extendModules {
      modules = [{
        networking.hostName = "apps";
        services.yeet-host.hostname = "apps";
        # For VM LAN networking, select the Ethernet uplink for a DHCP bridge.
        # services.yeet-host.lanUplink = "eth0";
      }];
    };
  };
}
```

Build the customized disk with `nix build .#nixosConfigurations.apps.config.system.build.image`. For subsequent updates, keep that site flake available on the host or deployment machine and use `nixos-rebuild switch --flake .#apps`. A host rollback uses `nixos-rebuild switch --rollback`. Payload generations have their own `yeet service rollback <service>` command.

On a fresh install, the installer configures `br0` when it can identify the wired uplink. The host gets its DHCP lease or static address on the bridge. For a custom host, set `services.yeet-host.lanUplink` to the physical interface, or set `services.yeet-host.lanBridge` to a bridge you already manage. Check the result with `catch vm-lan-bridge-status`. Each VM requesting `--net=lan` gets a TAP on that bridge when it starts. The NixOS configuration provides the shared service network, its DNS server, IP forwarding and Docker’s startup order. Yeet adds the network interfaces and unit dependencies specific to each payload as it starts.

Yeet host data and app files live under `/var/lib/yeet`. Yeet writes workload units and their boot dependency links directly under `/var/lib/yeet-systemd`. The NixOS module adds this tree to systemd’s unit search path. A stopped, boot-enabled service starts again on reboot. A disabled service stays disabled even if you start it manually. Include both directories and any custom service roots or Docker volumes in host backups. For ZFS-backed service roots, enable ZFS in the site configuration, assign the host a stable `networking.hostId`, configure the pool for import at boot (for example with `boot.zfs.extraPools`), and provision the parent dataset before passing `--zfs`. [ZFS service roots and recovery points](../yeet-nix/website/docs/concepts/zfs.mdx)

## Verify a deployment

`catch host-check` checks the containerd socket, Docker configuration, workload account and a real unprivileged Bubblewrap invocation. From a workstation, `CATCH_HOST=apps ./tests/payloads.sh` deploys a uniquely named sandboxed service, verifies a staged update and rollback by reading the running payload's output, copies a file both ways and cleans up. Set `YEET_CLI` to an absolute path to exercise a particular client build.

Run the fork's Go suite with `cd yeet-nix && mise exec -- go test ./...`. The [first-run playbook](../yeet-nix/website/docs/getting-started/first-run-validation.mdx) includes additional container, sandbox, cron, networking, VM and storage checks.

To time a real deploy, run `CATCH_HOST=apps python3 tests/benchmark-run.py > timings.csv` on your workstation. The benchmark times a fresh script and an updated script with the regular CLI. It discards two warm-up pairs, verifies the running payload after each deploy and removes each temporary service.
