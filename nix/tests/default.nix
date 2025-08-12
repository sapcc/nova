{
  pkgs,
  generateRootwrapConf,
  nixosModules,
  novaPkg,
  libvirt-custom
}:
let
  tests = {
    nova-chv-driver = pkgs.callPackage ./nova-chv-driver.nix {
      inherit nixosModules novaPkg generateRootwrapConf libvirt-custom;
    };
    chv-live-migration = pkgs.callPackage ./chv-live-migration.nix {
      inherit nixosModules novaPkg generateRootwrapConf libvirt-custom;
    };
  };
in
pkgs.lib.mapAttrs (_: v: pkgs.lib.recursiveUpdate v { meta.tag = "nix-integration-test"; }) tests
