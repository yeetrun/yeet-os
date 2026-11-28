# Yeet OS

Yeet lets you run apps on your own server from your laptop. Give `yeet run` a Compose file, a container image, or a script. Yeet starts it on the server, where it keeps running after you close your laptop.

**Yeet OS** is for a box or VM you want to dedicate to your homelab. It comes with NixOS, Yeet, and the tools to run your apps and look after the machine. You can install it over SSH and deploy your first app without writing any Nix.

## Install Yeet OS

[Download the latest installer ISO (x86-64)](https://github.com/yeetrun/yeet-os/releases/latest/download/yeet-os-installer.iso) · [SHA-256 checksum](https://github.com/yeetrun/yeet-os/releases/latest/download/SHA256SUMS)

Or download it from a terminal with curl.

```sh
curl -fL --retry 3 -o yeet-os-installer.iso https://github.com/yeetrun/yeet-os/releases/latest/download/yeet-os-installer.iso
```

You’ll need a spare disk of at least 10 GiB that you can erase, a wired network connection, and access to the machine’s screen or VM console. For SSH access after installation, have your GitHub username or an SSH public key handy. Yeet uses Tailscale to connect your laptop to the installed host. For automatic setup, create a Tailscale OAuth client with **Auth Keys write** and permission to assign your machine, Yeet server, and service tags. [Tailscale setup](yeet/website/docs/concepts/tailscale.mdx)

1. **Boot the installer.** Write the ISO to a USB stick and boot from it, or attach it to a VM as a virtual DVD. The installer gets an IP address with DHCP. Press **N** at the console if you need to set an address yourself.
2. **Open the installer on your laptop.** The console shows an SSH command like `ssh -p 2222 -t root@192.168.1.42` and a password for this boot. From the same LAN, run the command shown on **your** screen. Match the SSH fingerprint to the one on the console, then enter the password. The setup menu opens right in your terminal. You can also press **Enter** at the console to use the same menu there.
3. **Choose where your apps will live.** Select **Install a fresh host** and pick the OS disk to erase. For a single-disk machine, choose **Keep both on the OS disk**. If you want to be able to replace the boot disk later, put both Yeet’s host data and your app files on separate ZFS storage.
4. **Set up access and install.** Choose a hostname and network settings, then choose **Import keys from GitHub** and enter your username. Review the fingerprint of each key and choose the ones you use. You can also paste a public key. Set a password for the local console. Review the disk selection before confirming. Choose **Connect the machine, Yeet server and services** and paste your OAuth client secret. For a machine named `minilab`, setup suggests `yeet-minilab` for the Yeet server. Both names are editable.
5. **Boot from disk.** On the final screen, press **Enter** to power off. Once the machine is off, pull the USB stick or eject the ISO from the VM’s virtual CD/DVD drive. Turn it back on.

## Deploy your first app

On a Mac or Linux laptop connected to the same Tailscale network, install the CLI and point it at the host address from the installer. This example uses `yeet-minilab`.

```sh
curl -fsSL https://github.com/yeetrun/yeet-os/releases/latest/download/install.sh | sh
yeet config --host=yeet-minilab
mkdir -p ~/yeet-apps/web && cd ~/yeet-apps/web
yeet run web nginx:alpine
yeet service set web -p 80:80
curl http://web/
```

Yeet enrolls `web` on your tailnet when you deploy it. The second Yeet command publishes nginx’s HTTP port. With MagicDNS enabled and a tailnet grant for port 80, you should see the nginx welcome page. Use `ssh yeet@minilab` if your workstation has access to port 22 on the machine tag.

## Already using Yeet?

On a Mac or Linux workstation with an existing Yeet installation, run this once to replace the executable your shell uses. Your host and workspace settings carry over.

```sh
curl -fsSL https://github.com/yeetrun/yeet-os/releases/latest/download/install.sh | sh -s -- --dir "$(dirname "$(command -v yeet)")"
```

From then on, run `yeet upgrade` on your workstation. On a Yeet OS host, run `sudo yeet-os upgrade` to update the host, including its CLI and Catch. Workstations can still manage services on standalone Catch hosts through v0.11.18.

## What you can do with Yeet OS

- **Run the things you already have.** Deploy Docker Compose stacks, Dockerfiles, container images, Linux binaries, and scripts. Schedule a backup script as a cron job, or run a Linux microVM on a host with KVM support. Each app can be deployed independently of OS updates. [Workload guide](yeet/website/docs/payloads/index.mdx)
- **Manage apps over SSH.** Run `yeet ssh` from your laptop, or use `ssh yeet@yeet-home.local` on your LAN. Once you’re in, `yeet status`, `yeet logs web`, and `yeet restart web` work directly on the host. Bash completes service names with Tab. Tools like `btop`, `duf`, `lnav`, and `tcpdump` are already installed.
- **Replace a boot disk and keep your apps.** If your Yeet host data and workloads live on ZFS data disks, boot the ISO with the replacement disk attached and choose **Recover an existing host**. Import your pools, then select **Restore Yeet from my data disks** to see what the installer found. For encrypted datasets, you can supply your backed-up key over SSH so the host unlocks them on reboot. [Recovery guide](docs/install.md#move-an-existing-yeet-host-to-a-new-ssd) · [Watch encrypted disk recovery over SSH (video)](https://github.com/yeetrun/yeet-os/releases/download/v0.3.7/encrypted-zfs-recovery.mp4)
- **Customize your machine.** Put host-wide settings such as extra packages or network mounts in `/etc/nixos/local/default.nix`. Use Home Manager for your own shell and tools. Both carry forward when you upgrade. [Host settings](docs/host.md#change-settings-on-one-machine)

## Update the host

From the console or a regular SSH session (such as `ssh yeet@yeet-home.local`), check for a release and install it with these commands.

```sh
yeet-os check
sudo yeet-os upgrade
```

New packages are available in the same shell. The updater restarts affected services and tells you if a change needs a reboot, such as a new kernel. You choose when to reboot. If you need to go back, you can select the previous system at boot. [Upgrade over `yeet ssh`, defer an update, or roll back](docs/host.md#update-an-installed-host)
