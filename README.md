# Yeet OS

Yeet OS is a NixOS installer for machines you want to run apps on. If you already have a Linux server you like, install [Yeet](https://github.com/yeetrun/yeet) on it and keep your OS. If you’re setting up a dedicated box or VM, Yeet OS gives you the whole host, from the boot disk to the Yeet service.

Either way, you deploy from your laptop with the same `yeet run` command. On Yeet OS, Nix manages the machine and Yeet runs your apps. Shipping a new version of a script or container doesn’t require a Nix rebuild.

## Install Yeet OS

[Download the installer ISO for x86-64](https://github.com/yeetrun/yeet-os/releases/latest/download/yeet-os-installer.iso) · [SHA-256 checksum](https://github.com/yeetrun/yeet-os/releases/latest/download/SHA256SUMS)

To download the latest ISO from a terminal:

```sh
curl -fL --retry 3 -o yeet-os-installer.iso https://github.com/yeetrun/yeet-os/releases/latest/download/yeet-os-installer.iso
```

1. Write the ISO to a USB stick or attach it to a VM. Boot it with Ethernet connected. You’ll need a disk of at least 10 GiB that you can erase.
2. Look at the machine’s screen or VM console. It shows an SSH command, a host-key fingerprint, and a password for this boot. Run that command from a laptop on the same network, check the fingerprint when SSH asks, and enter the password. The installer opens in your terminal.
3. Choose **Install a fresh host**, pick your OS and data disks, and enter the name and SSH public key you want to use after reboot. The wizard walks you through Tailscale sign-in. Review your choices and install. Reboot when prompted and boot from the installed disk.

The ISO uses DHCP. Press **N** on the boot screen if you need to set up networking first, or **Enter** to install at the console. You can choose DHCP or a static address for the installed host.

If you’d like an installer that’s already on your tailnet when it boots, you can also [prepare a private ISO with Nix](docs/install.md#prepare-a-private-installer). The walkthrough shows that option and the disk wizard both installers use.

[![Walkthrough of the private installer, disk setup, and first deployment](docs/yeet-os-walkthrough.gif)](https://cdn.jsdelivr.net/gh/yeetrun/yeet-os@main/docs/yeet-os-walkthrough.mp4)

[Play the narrated walkthrough (1 min 42 sec)](https://cdn.jsdelivr.net/gh/yeetrun/yeet-os@main/docs/yeet-os-walkthrough.mp4)

## Deploy your first app

After reboot, use the Yeet CLI on your workstation. Set the host to the name on the installer’s final screen, then deploy nginx and check that it serves a page:

```sh
yeet config --host=yeet-home
yeet run web nginx:alpine -p 8080:80
yeet ssh -- curl -fsS http://127.0.0.1:8080/
```

You can also point `yeet run` at a local script, a binary, or a Compose file. [Install the CLI](docs/install.md#deploy-a-payload) · [Payload reference](yeet-nix/website/docs/payloads/index.mdx)

## Manage apps over SSH

Sign in with your workstation key, for example `ssh yeet@yeet-home.local`. From a workstation configured for this host, `yeet ssh` opens the same account over your tailnet. Releases through v0.3.0 used `root` for the login.

From that shell, check your apps or restart one:

```sh
yeet status
yeet restart web
```

These commands use the local host by default. Use `sudo -s` when you need a root shell. From your laptop, keep using the host’s Tailscale name.

Run `duf /` to see how much space is left on the root filesystem. Use `lnav` to browse logs and `ioping -c 3 ~` to check storage latency. The host includes `htop` and `btop` for a quick look at load, `iostat` and `smartctl` for disks, and `dig`, `mtr`, `iperf3` and `tcpdump` for network troubleshooting. You can also use `tmux`, `vim`, `git`, `jq` and `rsync` straight from your SSH session.

In Bash, press Tab to complete commands, flags, and local service names. For example, type `yeet stat` and press Tab to get `yeet status`. After `yeet restart `, press Tab twice to list matching services. If you already tried Tab in this shell before upgrading from v0.3.5, run `complete -r yeet` once to clear Bash’s cached fallback.

## Update the host

From the console or a regular SSH connection (for example, `ssh yeet@yeet-home.local`), run:

```sh
yeet-os check
sudo yeet-os upgrade
```

If you connected with `yeet ssh`, start the upgrade as a systemd job. That connection can drop when the Yeet host service restarts. The job keeps running, and you can reconnect and run `yeet-os status` to see the active version.

```sh
sudo systemd-run --unit=yeet-os-upgrade --collect --setenv=PATH=/run/current-system/sw/bin /run/current-system/sw/bin/yeet-os upgrade
```

The upgrade makes new commands available in your current shell and activates system services. Changed services may restart. If boot components changed, the updater tells you what takes effect on reboot. For a maintenance window, use `sudo yeet-os upgrade --boot-only` and reboot when ready. Hosts running v0.3.2 or earlier have the previous updater and stage upgrades for the next boot.

For machine-wide changes, edit `/etc/nixos/local/default.nix` and run `yeet-os rebuild`. This file can set the hostname, configure storage or add packages for everyone. For your own shell and tools, edit `~/.config/home-manager/home.nix` and run `home-manager switch`. [Host settings, rollback and upgrading from v0.2.2](docs/host.md#update-an-installed-host)

## Replace a boot disk

If you keep Yeet’s host data and app files on separate ZFS data disks, you can replace the OS disk and bring the same Yeet host back. Shut down the old host, leave the data disks attached, and boot the installer. Choose **Recover an existing host**, import your pools, then select **Restore Yeet from my data disks**. The wizard shows which apps it found before you confirm the replacement disk. For encrypted datasets, have your key backup ready. [Unlock your data disks over SSH](docs/install.md#recover-encrypted-zfs-data-disks)

[Watch encrypted ZFS recovery over SSH](https://github.com/yeetrun/yeet-os/releases/download/v0.3.7/encrypted-zfs-recovery.mp4) · [Watch the disk replacement (1 min 49 sec)](https://cdn.jsdelivr.net/gh/yeetrun/yeet-os@main/docs/legacy-migration.mp4) · [Supported storage layouts and recovery steps](docs/install.md#move-an-existing-yeet-host-to-a-new-ssd)

For a Proxmox disk image, instructions for updating the host, or the NixOS module for your own flake, see the [host guide](docs/host.md).
