{
  callPackage,
  lib,
  generateRootwrapConf,
  nixosModules,
  novaPkg,
  libvirt-custom,
}:
let
  chvModule = callPackage ./chv-module.nix { inherit libvirt-custom; };
  tests = {
    nova-chv-driver = callPackage ./nova-chv-driver.nix {
      inherit
        nixosModules
        novaPkg
        generateRootwrapConf
        chvModule
        ;
    };
    chv-live-migration = callPackage ./chv-live-migration.nix {
      inherit
        nixosModules
        novaPkg
        generateRootwrapConf
        chvModule
        ;
    };
    chv-numa = callPackage ./chv-numa.nix {
      inherit
        nixosModules
        novaPkg
        generateRootwrapConf
        chvModule
        ;
    };

    # Test implicit BDF assignment.
    # Test cases:
    #   Reboot VM via openstack and check BDF assignments
    #   VM Live migration and check BDF assignments
    chv-bdf-live-migration = callPackage ./chv-bdf-live-migration.nix {
      inherit
        nixosModules
        novaPkg
        generateRootwrapConf
        chvModule
        ;
      testScriptFile = ./chv-bdf-live-migration.py;
    };
  };
in
lib.mapAttrs (_: v: lib.recursiveUpdate v { meta.tag = "nix-integration-test"; }) tests
