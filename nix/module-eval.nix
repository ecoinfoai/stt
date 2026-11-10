# Evaluation check of the `services.stt` assertions (contracts/module.md section 2).
#
# Nothing is built: the NixOS configuration is only evaluated, once per systemd version, and the
# sentences of the failed assertions are collected. The `assert`s below run at evaluation time, so
# `nix flake check --no-build` fails as soon as the systemd version assertion goes missing.
{
  pkgs,
  nixpkgs,
  module,
}:
let
  inherit (pkgs) lib;

  withVersion =
    version:
    pkgs.systemd.overrideAttrs (_: {
      inherit version;
      __intentionallyOverridingVersion = true;
    });

  failedAssertions =
    systemdPackage:
    let
      evaluated = nixpkgs.lib.nixosSystem {
        system = pkgs.stdenv.hostPlatform.system;
        modules = [
          module
          {
            services.stt = {
              enable = true;
              # Only the assertions are evaluated, so the real server environment is not instantiated.
              package = pkgs.hello;
            };
            boot.isContainer = true;
            system.stateVersion = "25.11";
          }
        ]
        ++ lib.optional (systemdPackage != null) { systemd.package = systemdPackage; };
      };
    in
    map (failed: failed.message) (lib.filter (candidate: !candidate.assertion) evaluated.config.assertions);

  versionSentences =
    systemdPackage:
    lib.filter (message: lib.hasPrefix "services.stt needs systemd 257 or later" message) (failedAssertions systemdPackage);

  tooOld = versionSentences (withVersion "256.10");

  checked =
    assert lib.assertMsg (builtins.length tooOld == 1) "systemd 256.10 must fail the version assertion exactly once";
    assert lib.assertMsg (lib.hasInfix "256.10" (builtins.head tooOld)) "the sentence must name the version 256.10";
    assert lib.assertMsg (versionSentences (withVersion "257") == [ ]) "systemd 257 must pass the version assertion";
    assert lib.assertMsg (versionSentences null == [ ]) "the pinned nixpkgs systemd must pass the version assertion";
    true;
in
assert checked;
pkgs.runCommand "stt-module-eval" { } "touch $out"
