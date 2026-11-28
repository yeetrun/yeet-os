{ lib, pkgs, firmware, modulesPath, ... }:
{
  imports = [ (modulesPath + "/profiles/all-hardware.nix") ];
  services.tailscale.enable = true;
  services.tailscale.openFirewall = true;
  services.yeet-host.enable = true;
  # The disk wizard or the local host module supplies the hostname.
  networking.hostName = lib.mkOverride 1100 "";
  networking.useDHCP = lib.mkDefault true;
  boot.initrd.availableKernelModules = [ "ahci" "nvme" "xhci_pci" "uas" "usb_storage" "sd_mod" "ata_piix" "virtio_pci" "virtio_scsi" "virtio_blk" "vmw_pvscsi" "hv_storvsc" ];
  fileSystems."/" = { device = "/dev/disk/by-label/YEETROOT"; fsType = "ext4"; };
  fileSystems."/boot" = lib.mkIf (firmware == "uefi") {
    device = "/dev/disk/by-label/YEETEFI"; fsType = "vfat"; options = [ "umask=0077" ];
  };
  boot.loader.systemd-boot.enable = firmware == "uefi";
  boot.loader.efi.canTouchEfiVariables = false;
  boot.loader.grub = lib.mkIf (firmware == "bios") {
    enable = true;
    # The installer writes the BIOS boot sector to the selected whole disk.
    devices = [ "nodev" ];
  };
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    wideArea = false;
    openFirewall = false;
    # Site configuration selects the hardware uplinks after disk discovery.
    allowInterfaces = lib.mkDefault [ "lo" ];
    publish = { enable = true; addresses = true; };
    extraServiceFiles.ssh = ''
      <service-group>
        <name replace-wildcards="yes">%h</name>
        <service><type>_ssh._tcp</type><port>22</port></service>
      </service-group>
    '';
  };
  services.openssh = {
    enable = true;
    settings = { PasswordAuthentication = false; KbdInteractiveAuthentication = false; };
  };
  services.qemuGuest.enable = true;
  boot.kernelParams = [ "console=tty0" "console=ttyS0,115200" ];
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  environment.systemPackages = [ (pkgs.callPackage ./setup-tool.nix {}) ];
  environment.etc."issue".text = ''
    Yeet OS
    Sign in as yeet and run sudo yeet-setup for workstation setup and host enrollment.
  '';
  system.stateVersion = "26.05";
}
