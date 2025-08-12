{
  pkgs,
  generateRootwrapConf,
  nixosModules,
  novaPkg,
  libvirt-custom
}:
let
  chvModule = pkgs.callPackage ./chv-module.nix { inherit libvirt-custom; };
  tests = {
    nova-chv-driver = pkgs.callPackage ./nova-chv-driver.nix {
      inherit nixosModules novaPkg generateRootwrapConf libvirt-custom;
    };
    chv-live-migration = pkgs.callPackage ./chv-live-migration.nix {
      inherit nixosModules novaPkg generateRootwrapConf libvirt-custom;
    };
    chv-numa = pkgs.callPackage ./chv-numa.nix {
      inherit nixosModules novaPkg generateRootwrapConf chvModule;
    };
  };
in
pkgs.lib.mapAttrs (_: v: pkgs.lib.recursiveUpdate v { meta.tag = "nix-integration-test"; }) tests
