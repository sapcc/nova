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
    cloud-hypervisor = {
      url = "github:cyberus-technology/cloud-hypervisor?ref=gardenlinux";
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
      cloud-hypervisor,
      libvirt-custom,
      ...
    }:
    flake-utils.lib.eachSystem [ "x86_64-linux" ] (
      system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [
            (_: prev: {
              cloud-hypervisor = cloud-hypervisor.packages.${system}.default.overrideAttrs (old: {
                env = (old.env or { }) // {
                  CARGO_PROFILE_RELEASE_DEBUG_ASSERTIONS = "true";
                  CARGO_PROFILE_RELEASE_OPT_LEVEL = 2;
                  CARGO_PROFILE_RELEASE_OVERFLOW_CHECKS = "true";
                  CARGO_PROFILE_RELEASE_LTO = "thin";
                };
              });
            })
          ];
        };

        nixosModules = openstack-nix.nixosModules.${system};
        openstackPackages = openstack-nix.packages.${system};
        generateRootwrapConf = openstack-nix.lib.${system}.generateRootwrapConf;

        novaSrc = pkgs.lib.cleanSourceWith {
          src = ./.;
          filter =
            path: type:
            let
              baseName = baseNameOf path;
            in
            # Exclude .nix files and nix/ directory
            !(pkgs.lib.hasSuffix ".nix" baseName) && !(baseName == "nix" && type == "directory");
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

        # test / lint all code below nix folder
        testSrcNix = pkgs.lib.cleanSourceWith {
          src = ./nix;
          filter =
            path: type:
            let
              baseName = baseNameOf path;
            in
            # Include .nix files only
            (pkgs.lib.hasSuffix ".nix" baseName);
        };

        testSrcPython = pkgs.lib.cleanSourceWith {
          src = ./nix;
          filter =
            path: type:
            let
              baseName = baseNameOf path;
            in
            # Include .py files only
            (pkgs.lib.hasSuffix ".py" baseName);
        };

        deadnix =
          pkgs.runCommand "deadnix"
            {
              nativeBuildInputs = [ pkgs.deadnix ];
            }
            ''
              deadnix -L ${testSrcNix} --fail
              mkdir $out
            '';

        nixFormat =
          pkgs.runCommand "nix-format"
            {
              nativeBuildInputs = with pkgs; [
                nix
                nixfmt-tree
              ];
            }
            ''
              treefmt --ci ${testSrcNix}
              mkdir $out
            '';

        pythonFormat =
          pkgs.runCommand "python-format"
            {
              nativeBuildInputs = with pkgs; [ ruff ];
            }
            ''
              ruff format --check ${testSrcPython}
              mkdir $out
            '';

        pythonLint =
          pkgs.runCommand "python-lint"
            {
              nativeBuildInputs = with pkgs; [ ruff ];
            }
            ''
              ruff check ${testSrcPython}
              mkdir $out
            '';

        pythonTypes =
          pkgs.runCommand "python-types"
            {
              nativeBuildInputs = with pkgs; [ pyright ];
            }
            ''
              pyright ${testSrcPython}
              mkdir $out
            '';

        typos =
          pkgs.runCommand "spellcheck"
            {
              nativeBuildInputs = [ pkgs.typos ];
            }
            ''
              typos ${testSrcPython}
              typos ${testSrcNix}
              mkdir $out
            '';

        allChecks = pkgs.symlinkJoin {
          name = "combined-checks";
          paths = [
            deadnix
            nixFormat
            pythonFormat
            pythonLint
            pythonTypes
            typos
          ];
        };

      in
      {
        formatter = pkgs.nixfmt-tree;
        tests = import ./nix/tests/default.nix {
          inherit (pkgs) callPackage lib;
          inherit
            nixosModules
            novaPkg
            generateRootwrapConf
            libvirt-custom
            ;
        };

        checks = {
          default = allChecks;
        };
      }
    );
}
