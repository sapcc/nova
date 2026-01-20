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
      inherit nixosModules novaPkg generateRootwrapConf chvModule;
    };
    chv-live-migration = pkgs.callPackage ./chv-live-migration.nix {
      inherit nixosModules novaPkg generateRootwrapConf chvModule;
    };
    chv-numa = pkgs.callPackage ./chv-numa.nix {
      inherit nixosModules novaPkg generateRootwrapConf chvModule;
    };

    # Test implicit BDF assignment.
    # Test cases:
    #   Reboot VM via openstack and check BDF assignments
    #   VM Live migration and check BDF assignments
    chv-bdf-live-migration = pkgs.callPackage ./chv-bdf-live-migration.nix {
      inherit nixosModules novaPkg generateRootwrapConf chvModule;
      testScriptFile = ./chv-bdf-live-migration.py;
    };
  };
in
pkgs.lib.mapAttrs (_: v: pkgs.lib.recursiveUpdate v { meta.tag = "nix-integration-test"; }) tests
