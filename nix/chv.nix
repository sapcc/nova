{
  # from flake inputs
  craneLib,
  cloud-hypervisor-src,
  rustToolchain,
  # from nixpkgs,
  lib,
  openssl,
  pkg-config,
  # other
  cloud-hypervisor-meta,
}:
let
  # Crane lib with proper Rust toolchain
  craneLib' = craneLib.overrideToolchain rustToolchain;

  commonArgs = {
    meta = cloud-hypervisor-meta;

    src = craneLib'.cleanCargoSource cloud-hypervisor-src;

    #patches =
    #  let
    #    patchSrc = ./patches/cloud-hypervisor;
    #  in
    #  (lib.pipe patchSrc [
    #    builtins.readDir
    #    builtins.attrNames
    #    # To fully-qualified path.
    #    (map (f: "${patchSrc}/${f}"))
    #  ]);

    nativeBuildInputs = [
      pkg-config
    ];
    buildInputs = [
      openssl
    ];
    # Fix build. Reference:
    # - https://github.com/sfackler/rust-openssl/issues/1430
    # - https://docs.rs/openssl/latest/openssl/
    OPENSSL_NO_VENDOR = true;
  };

  # Downloaded and compiled dependencies.
  cargoArtifacts = craneLib'.buildDepsOnly (
    commonArgs
    // {
      pname = "cloud-hypervisor-deps";
    }
  );

  cargoPackageKvm = craneLib'.buildPackage (
    commonArgs
    // {
      inherit cargoArtifacts;
      pname = "cloud-hypervisor";
      # Don't execute tests here. We want this in a dedicated step.
      doCheck = false;
      cargoExtraArgs = "--features kvm";
    }
  );
in
cargoPackageKvm
