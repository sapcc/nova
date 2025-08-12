{ libvirt-custom }:
{ pkgs, ... }:
{
  virtualisation.libvirtd.package = pkgs.libvirt.overrideAttrs (_: {
    src = libvirt-custom;
    doInstallCheck = false;
    doCheck = false;
    patches = [
      ./0001-meson-patch-in-an-install-prefix-for-building-on-nix.patch
      ./0002-substitute-zfs-and-zpool-commands.patch
    ];
  });

  systemd.services.virtchd.wantedBy = [ "multi-user.target" ];
  systemd.services.virtlogd.wantedBy = [ "multi-user.target" ];
  systemd.services.virtnodedevd.wantedBy = [ "multi-user.target" ];
  systemd.sockets.virtproxyd-tcp.wantedBy = [ "sockets.target" ];

  systemd.tmpfiles.settings =
    let
      chv-ovmf = pkgs.OVMF-cloud-hypervisor.fd;
    in
    {
      "10-chv" = {
        "/usr/share/cloud-hypervisor/CLOUDHV_EFI.fd" = {
          "L+" = {
            argument = "${chv-ovmf}/FV/CLOUDHV.fd";
          };
        };
        "/var/log/libvirt/ch" = {
          "D" = {
            user = "root";
            group = "root";
            mode = "0755";
          };
        };
      };
    };
}
