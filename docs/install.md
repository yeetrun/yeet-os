# Install Yeet OS

[Download the x86-64 installer](https://github.com/yeetrun/yeet-os/releases/latest/download/yeet-os-installer.iso) and write it to USB or attach it as a virtual CD. It boots with UEFI or legacy BIOS. Have a spare OS disk of at least 10 GiB. Yeet host data and app files can live on separate disks. To verify the download, save the [checksum file](https://github.com/yeetrun/yeet-os/releases/latest/download/SHA256SUMS) beside it and run `shasum -a 256 -c SHA256SUMS`.

## Connect to the installer

Boot the machine on a wired network with DHCP. The physical or VM console shows a command such as `ssh -p 2222 -t root@192.168.1.42`, an SSH host-key fingerprint, and a random password. From a computer on the same network, run the command displayed on **your** screen. Check the fingerprint, then enter the displayed password. This opens the setup wizard. You can do the rest over SSH, including disk selection and Tailscale sign-in. Use a trusted installation network.

Press **N** on the console to change live network settings if needed, or **Enter** to use the wizard locally. The SSH password and host key change whenever you reboot the ISO. If SSH remembers an earlier installer at that address, check the new fingerprint at the console before removing the old entry with `ssh-keygen -R '[192.168.1.42]:2222'`, substituting the actual address.

Choose a hostname and DHCP or static IPv4 for the installed host. At **SSH into the installed host**, choose **Import keys from GitHub** and enter the GitHub username where you keep your public SSH keys. For each fingerprint, choose **Use this key** or **Skip this key**. To check your laptop’s fingerprint, run `ssh-keygen -lf ~/.ssh/id_ed25519.pub` (use your own public key’s path). Anyone with a matching private key can sign in as `yeet` and use sudo. The installer copies the keys you select once. Later changes on GitHub do not change access on the host.

You can also choose **Paste a public key** or **Use the local console only**. Set a separate local console password. To join Tailscale, open the login URL or scan the QR code when prompted, or paste a single-use preauthorized, non-ephemeral auth key for `tag:catch` during setup. The installed system accepts SSH keys on port 22. [Tailscale permissions](../yeet-nix/website/docs/concepts/tailscale.mdx)

## Prepare a private installer

Use this option for a machine you want to reach over Tailscale as soon as it boots. The workstation needs Nix with flakes and access to an x86-64 Linux builder. A Mac can run the wizard and use a configured remote Linux builder.

```sh
nix --extra-experimental-features 'nix-command flakes' run github:yeetrun/yeet-os#prepare
```

1. Choose a hostname (the suggested name is `yeet-home`) and your workstation’s SSH **public** key. A fresh install uses that hostname for the live OS, the installed OS, and the name Yeet requests on Tailscale. When recovering a host, Yeet reuses its saved Tailscale identity.
2. Choose wired DHCP or static IPv4. For static networking, enter the host address with prefix, gateway and DNS servers. You can leave the adapter blank. The live installer selects a sole Ethernet adapter automatically or offers a choice on its console when several are present. The live installer and installed host both use these settings.
3. Paste a **single-use, preauthorized, non-ephemeral** Tailscale auth key for `tag:catch`. Your tailnet policy must let your workstation reach the installer on TCP 22 and reach your Yeet host after installation. [Create an auth key](https://tailscale.com/kb/1085/auth-keys) · [Yeet tailnet grants](../yeet-nix/website/docs/concepts/tailscale.mdx)
4. Choose where to save the ISO. Nix builds the public system, then the workstation adds the enrollment key to the private ISO. Anyone who can read the ISO can use its key until consumed or expired. Keep it outside the repository. Write it to USB or attach it as a virtual CD, then boot it.

From your workstation, use the assigned tailnet name. OpenSSH authenticates with the public key you selected. If the machine cannot get online, the physical or hypervisor console offers the same storage wizard.

```sh
ssh -t root@yeet-home
```

The SSH session opens the installer menu directly. If your public key has a custom filename, use its matching private key with `ssh -t -i ~/.ssh/your-key root@yeet-home`. In Proxmox, select the VM and open **Console** to use the same menu.

For a scripted build, pass `--profile machine.json --auth-key-file /private/yeet-host.key --output yeet-home.iso` after `nix run .#prepare --`. The public profile has four fields. Choose a unique eight-digit nonzero hexadecimal host ID for each machine.

```json
{
  "hostname": "yeet-home",
  "hostId": "21b2f9ae",
  "sshPublicKey": "ssh-ed25519 REPLACE_WITH_YOUR_WORKSTATION_PUBLIC_KEY",
  "network": { "mode": "dhcp" }
}
```

## Choose storage on the machine

Select **Install a fresh host**, then identify the OS disk by size, model and serial. The installer partitions the selected disk. Pool members, ZFS volumes used as VM disks and disks containing a Yeet OS root are excluded from the erase list.

| Choice | What lives there | Default |
| --- | --- | --- |
| Yeet host data | Host identity, deployment database, registry and VM image cache. A custom layout also keeps payload systemd units and Docker/containerd storage here, including named volumes. | `/var/lib/yeet` |
| App files | One working directory per payload, including its files and VM disks managed under that directory. | `<Yeet host data>/services` |

**Keep both on the OS disk** uses the defaults. **Choose where to store host data and app files** lets you choose each independently. Select a directory on the OS disk, an empty dataset, or a new child of an existing dataset. For example, create `tank/yeet-data` for host data and `tank/yeet-services` for app files. Choosing a dedicated dataset as the services root lets Yeet create a child dataset per service. A plain directory inside a dataset uses ordinary service directories.

Under **Manage ZFS pools or open shell**, import an offline pool by name and ID. To create a pool, open the ZFS shell, check disk serials with `lsblk`, then use `zpool` and `zfs` to build the desired topology. If your shell commands mounted filesystems, export the new pool before leaving the shell. Type `exit` to return, then import it through the wizard so the live system leaves its filesystems unmounted. You can keep unrelated datasets in the same pool. The host mounts the selected storage and its service children.

Review the exact OS disk and the action for each storage location. Type the OS disk path to confirm. Setup prepares the selected NixOS generation before partitioning. When installation completes, reboot from the final screen. Select the installed disk at boot or detach the installer once the machine restarts. On a fresh install with prepared media, your Yeet host keeps the Tailscale identity you used to reach the installer. [Storage choices](screenshots/installer-payload-storage.png)

## Find the installed host on the LAN

After booting from disk, a machine named `yeet-home` publishes `yeet-home.local` and the Bonjour SSH service `_ssh._tcp` through Avahi. From the same LAN, sign in with `ssh yeet@yeet-home.local`. The installer key signs you into `yeet`. The console password also belongs to `yeet`; SSH uses public keys. Run `sudo -s` for a root shell. On a Mac, `dns-sd -B _ssh._tcp local` browses available SSH services. During installation, use the SSH command on the console for the public ISO, or the tailnet name for a private ISO.

The installer records physical Ethernet-type interfaces in `/etc/nixos/host.json`. Static installations use the selected adapter. DHCP installations use the detected hardware adapters. If you replace or rename an adapter, update the list and run `sudo yeet-os rebuild`, then reboot. Discovery follows the Yeet LAN bridge when configured. Payload networks are excluded. Installed hosts also enable time synchronization, weekly SSD TRIM, the NixOS firewall and the QEMU guest agent.

## Recover an existing Yeet host

Choose **Recover an existing host** at the welcome screen. Shut down the original host before bringing its identity online elsewhere. For remote recovery with a private ISO, use a distinct temporary hostname and a new enrollment key. [Recovery screen](screenshots/installer-recovery.png)

- **Repair my Yeet OS boot disk** if its root filesystem and installed system generation are intact. Boot the installer in the same firmware mode, select the original disk and confirm the hostname. This reinstalls the bootloader. Eject the installer and start the original disk.
- **Restore Yeet from my data disks** if you replaced the OS disk and still have your original ZFS data disks. Import the original pools, then select the dataset where Yeet stored its host data. The wizard uses saved settings when it finds them. Otherwise, it reads the app list from your disks and asks what should start at boot.

After recovery, you can use the same Yeet address from your workstation. If you used a private ISO, remove its temporary installer node from your tailnet when finished. To prepare for a future disk swap, keep both Yeet host data and app files on storage that will survive it. Apps that use external drives or remote volumes still need access to those files.

### Move an existing Yeet host to a new SSD

[Watch the recovery walkthrough](legacy-migration.mp4) to see **Restore Yeet from my data disks** in the Proxmox console. It shows how to import the original pool, recognize the replacement SSD and verify the same app file after reboot. It also shows the app list Yeet displays when a disk has no saved OS settings.

Boot the public ISO and use the LAN login on its console. If you use a private installer instead, give it a temporary hostname for SSH access.

1. Shut down the previous host. Replace its boot SSD, leave the data disks attached, and boot the ISO. Connect over SSH or use the physical console.
2. Choose **Recover an existing host → Manage ZFS pools or open shell** and import each original pool. Then choose **Restore Yeet from my data disks**. Select the ZFS dataset where Yeet stored its host data. You can recognize it by `db.json` and `tsnet/tailscaled.state`.
3. If Yeet finds saved settings, review them and continue to the replacement disk. Otherwise, browse the list of apps and select **Continue to startup settings**. Yeet checks your apps, VM launch files and disks. For Compose, choose the original Docker and containerd datasets. The installer checks stored projects, named volumes, bind sources and device paths. If a saved network uses a bridge, select the Ethernet uplink for that bridge.
4. Enter the installed machine’s LAN hostname and review the inferred dataset and network mappings. Select the native services, timers and VMs to enable at boot. Docker keeps each restored container’s restart policy. The original OS held systemd enablement and any administrator drop-ins. Reapply site-specific settings that lived only on that disk from a backup.
5. Confirm the old host is offline. Select the replacement OS disk by serial and type its path to authorize erasure. After reboot, use the same Tailscale address to reach your Yeet host. A recovery record saved with the restored units supports future SSD replacements.

Recovery without saved settings supports Yeet host data at the top level of a ZFS filesystem, service directories under it or on separate ZFS filesystems, component-based Firecracker VMs, and Compose projects on dedicated Docker and containerd datasets using Docker’s containerd image store. Applications whose data lived only on the removed SSD need that data restored first. A path with a missing backing filesystem, an unsupported runtime, or a missing hardware device stops the install before disk erasure. Host SSH keys, unrelated system services and hypervisor guests are outside the Yeet workload inventory.

### Recover encrypted ZFS data disks

Before removing the old boot disk, check which files unlock your pools:

```sh
ssh root@OLDHOST 'zfs get -H -t filesystem,volume -o name,value keylocation'
```

A `file:///…` entry on an encryption root identifies the original key file. Copy each distinct file to private storage you can access without those pools. From a workstation with an encrypted backup drive mounted, substitute its directory and the path reported by ZFS:

```sh
(
  set -eu
  umask 077
  cd /path/to/encrypted-backup-drive
  backup=$(mktemp -d yeet-keys.XXXXXXXX)
  scp root@OLDHOST:/path/to/original.key "$backup/pool.key"
  ssh -T root@OLDHOST 'cmp -s - /path/to/original.key' < "$backup/pool.key"
  printf 'Verified backup: %s/%s/pool.key\n' "$PWD" "$backup"
)
```

For a passphrase with `keylocation=prompt`, keep the original passphrase in your password manager. ZFS can verify a key while the dataset is in use:

```sh
ssh root@OLDHOST 'zfs load-key -n -L file:///path/to/original.key pool/root'
```

In the installer choose **Recover an existing host → Manage ZFS pools or open shell → Import an existing pool** for each data pool. Open **Encrypted ZFS recovery** and select the dataset you want to unlock. Leave that screen open. In a second terminal on your workstation, copy the command shown and replace `KEYFILE` with the path to your backup. The installer fills in its SSH address, port and the dataset name. For example:

```sh
ssh -T -p 2222 root@192.0.2.10 yeet-zfs-key tank/apps < "$HOME/Downloads/backup.key"
```

For the generic ISO, verify the SSH fingerprint against the live console and use the installer login password. The wizard changes to **Key verified** as soon as the dataset unlocks. If the upload reports an incorrect key, leave the wizard open and try the correct file. Repeat for each required encryption root, including roots holding Docker bind mounts or VM disks. Multiple roots can use the same backup file. Send raw keys as binary files, hex keys in their original text format, and passphrases as a single line through stdin. Return to **Restore Yeet from my data disks**. Before you confirm the install, the review lists the verified keys and explains how the host will unlock your data on reboot.

Setup verifies the keys before erasing the replacement SSD. It stores the required keys in a root-only directory at `/etc/yeet-zfs-keys` on that SSD. The installed host reads them after pool import and before mounting the data filesystems. The keys stay outside the Nix store, ISO, initrd, and recovery record. The pools retain their original key properties. Keep your independent key backup for the next disk replacement. Someone with the replacement SSD and the data disks can unlock the data.

## Deploy a payload

On a Mac or Linux computer signed into that tailnet, substitute the name shown on the completion screen.

```sh
curl -fsSL https://yeetrun.com/install.sh | sh
yeet config --host=yeet-home.example.ts.net
yeet version
yeet run web nginx:alpine -p 8080:80
yeet ssh -- curl -fsS http://127.0.0.1:8080/
```

If the network goes down after the disk installation finishes, boot the installed disk, sign in locally as `yeet` and run `sudo yeet-setup` to finish enrollment. Run `sudo yeet-setup` from the installed host to revisit workstation setup. The wizard saves the machine settings in `/etc/nixos`. Put host-wide NixOS changes in `/etc/nixos/local/default.nix`, then run `sudo yeet-os rebuild` and reboot. Use `sudo yeet-os upgrade` to stage a new OS release. Payload deploys use the ordinary Yeet CLI. Deployments requesting a separate Tailscale identity for a service also need a service provisioning credential as described in the [host instructions](host.md#connect-the-host-to-tailscale-and-deploy-an-app).

## Unattended install

The same public ISO accepts an unattended seed. The live SSH login is disabled when booting with a seed. To build the generic ISO yourself on x86-64 Linux, run `nix build .#installer` in this checkout.

Prepare a second volume labeled `YEETSEED`. Put these two files at its root. Anyone with access to this volume can read the enclosed key until it is consumed or expires.

- `yeet-host.key` holds a single-use, **non-ephemeral**, preauthorized Tailscale auth key with the `tag:catch` tag.
- `install.json` names the disk to erase and the host to enroll. Replace the public key and disk identifiers below with the destination machine's actual values. Check them with `lsblk -d -o PATH,MODEL,SERIAL,SIZE` from the installer rescue shell.

```json
{
  "version": 1,
  "disk": "/dev/sda",
  "erase": "/dev/sda",
  "serial": "YOUR-DISK-SERIAL",
  "hostname": "yeet-home",
  "ssh_public_key": "ssh-ed25519 REPLACE_WITH_YOUR_WORKSTATION_PUBLIC_KEY",
  "auth_key_file": "yeet-host.key",
  "on_success": "poweroff"
}
```

The serial is optional. A stable `/dev/disk/by-id/…` path also works when supplied in both disk fields. Use a public SSH key, a `password_hash` containing a SHA-512 or yescrypt hash, or both. SSH accepts key authentication only. The completion action is `reboot` by default. `poweroff` lets an operator detach both volumes before starting the installed disk. `stay` leaves the live environment running.

To make the seed into a virtual CD, put just those two files in a private directory called `seed` and run:

```sh
umask 077
xorriso -as mkisofs -V YEETSEED -J -r -o yeet-seed.iso seed/
```

Attach the installer and the seed, choose the installer as the boot device and start the machine. Follow progress on the console with `journalctl -fu yeet-autoinstall`. An unattended seed refuses a mounted disk and a disk already containing a Yeet OS root. For an explicitly mounted seed, the rescue shell also accepts `yeet-setup --config /path/to/install.json`.

After the host powers off, eject both volumes, boot from disk, and run the workstation commands above. Remove the temporary seed and its key after provisioning.
