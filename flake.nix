{
  description = "Cloud Hypervisor driver for OpenStack Nova";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-25.05";
    openstack-nix = {
      # url = "git+file:<path/to/openstack-nix>";
      url = "git+https://github.com/cobaltcore-dev/openstack-nix.git";
      # We have observed problems if nixpkgs of the consuming project and
      # nixpkgs of openstack-nix are diverged too much. Therefore, use the
      # nixpkgs of the consuming project.
      inputs.nixpkgs.follows = "nixpkgs";
    };
    cloud-hypervisor-src = {
      url = "github:cyberus-technology/cloud-hypervisor?ref=gardenlinux";
      flake = false;
    };
    # Nix tooling to build cloud-hypervisor.
    crane.url = "github:ipetkov/crane/master";
    # Get proper Rust toolchain, independent of pkgs.rustc.
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    libvirt-custom = {
      url = "git+https://github.com/cyberus-technology/libvirt.git?ref=gardenlinux&submodules=1";
      # url = "git+file:<path/to/libvirt>?submodules=1";
      flake = false;
    };
  };

  outputs =
    {
      nixpkgs,
      flake-utils,
      openstack-nix,
      cloud-hypervisor-src,
      libvirt-custom,
      crane,
      rust-overlay,
      ...
    }:
    flake-utils.lib.eachSystem [ "x86_64-linux" ] (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [
            (_: prev: {
              cloud-hypervisor = pkgs.callPackage ./nix/chv.nix {
                inherit cloud-hypervisor-src;
                craneLib = crane.mkLib pkgs;
                rustToolchain = rust-bin.stable.latest.default;
                cloud-hypervisor-meta = prev.cloud-hypervisor.meta;
              };
            })
          ];
        };
        rust-bin = (rust-overlay.lib.mkRustBin { }) pkgs;
        nixosModules = openstack-nix.nixosModules.${system};
        openstackPackages = openstack-nix.packages.${system};
        generateRootwrapConf = openstack-nix.lib.${system}.generateRootwrapConf;

        novaSrc = pkgs.lib.cleanSourceWith {
          src = ./.;
          filter = path: type:
            let
              baseName = baseNameOf path;
            in
              # Exclude .nix files and nix/ directory
              !(pkgs.lib.hasSuffix ".nix" baseName) &&
              !(baseName == "nix" && type == "directory");
        };

        # The PBR setup does not work on the plain source code because no
        # package version can be determined.
        # We add a PKG-INFO file with the missing information to make it work.
        # We use the version info of the original Nova package from
        # openstack-nix.
        fixedNovaSrc = pkgs.runCommand "add-package-info" { } ''
          mkdir -p $out

          cp -r ${novaSrc}/. $out

          cat >$out/PKG-INFO <<EOL
          Metadata-Version: 2.1
          Name: nova
          Version: ${openstackPackages.nova.version}
          EOL
        '';

        novaPkg = openstackPackages.nova.overrideAttrs (_: {
          src = fixedNovaSrc;
          doInstallCheck = false;
        });
      in
      {
        formatter = pkgs.nixfmt-rfc-style;
        tests = import ./nix/tests/default.nix {
          inherit
            pkgs
            nixosModules
            novaPkg
            generateRootwrapConf
            libvirt-custom
            ;
        };
      }
    );
}
