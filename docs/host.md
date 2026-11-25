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
  --ciuser root --sshkeys "$pubkey" --ipconfig0 ip=dhcp --boot order=scsi0
qm resize "$vmid" scsi0 40G
qm start "$vmid"
qm guest cmd "$vmid" network-get-interfaces
```

Wait for the guest agent, then connect to the DHCP address with SSH. The image accepts a NoCloud public key and grows its root filesystem on boot.

## Connect the host to Tailscale and deploy an app

Use a Tailscale auth key with the `tag:catch` tag for the host. For a fleet, choose a unique `services.yeet-host.hostname` in each machine's configuration before enrollment. The generic image uses `yeet-os`. Run these commands in Bash as root **inside the guest**. The enrollment file lives in tmpfs.

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

## Customize or update the host

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

Yeet host data and app files live under `/var/lib/yeet`. Yeet writes workload units and their boot dependency links directly under `/var/lib/yeet-systemd`. The NixOS module adds this tree to systemd’s unit search path. A stopped, boot-enabled service starts again on reboot. A disabled service stays disabled even if you start it manually. Include both directories and any custom service roots or Docker volumes in host backups. For ZFS-backed service roots, enable ZFS in the site configuration, assign the host a stable `networking.hostId`, configure the pool for import at boot (for example with `boot.zfs.extraPools`), and provision the parent dataset before passing `--zfs`. [ZFS service roots and recovery points](../yeet-nix/website/docs/concepts/zfs.mdx)

## Verify a deployment

`catch host-check` checks the containerd socket, Docker configuration, workload account and a real unprivileged Bubblewrap invocation. From a workstation, `CATCH_HOST=apps ./tests/payloads.sh` deploys a uniquely named sandboxed service, verifies a staged update and rollback by reading the running payload's output, copies a file both ways and cleans up. Set `YEET_CLI` to an absolute path to exercise a particular client build.

Run the fork's Go suite with `cd yeet-nix && mise exec -- go test ./...`. The [first-run playbook](../yeet-nix/website/docs/getting-started/first-run-validation.mdx) includes additional container, sandbox, cron, networking, VM and storage checks.

To time a real deploy, run `CATCH_HOST=apps python3 tests/benchmark-run.py > timings.csv` on your workstation. The benchmark times a fresh script and an updated script with the regular CLI. It discards two warm-up pairs, verifies the running payload after each deploy and removes each temporary service.
