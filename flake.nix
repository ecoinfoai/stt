{
  description = "stt — local lecture transcription pipeline (yt-dlp + faster-whisper)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix/ddc400a6883b042646d0940e65971bb7f7e96107";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix/7aba73a3d85454f1118f8cf9de97979b4d26bcac";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs/a17819c85163988e063806ffc4f6d2716bc8ead8";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, pyproject-nix, uv2nix, pyproject-build-systems }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            # requires-python >=3.10; 3.12 is the newest interpreter with
            # wheels for every locked dependency (ctranslate2, onnxruntime, av).
            pkgs.python312
            pkgs.uv
            # yt-dlp shells out to ffmpeg to extract audio (stt fetch).
            pkgs.ffmpeg
            # YouTube extraction without a JavaScript runtime is deprecated
            # and drops formats; deno is the one yt-dlp enables by default.
            pkgs.deno
          ];

          env = {
            # uv's downloadable interpreters are dynamically linked against
            # /lib64/ld-linux-x86-64.so.2, which does not exist on NixOS.
            UV_PYTHON_PREFERENCE = "only-system";
          };

          shellHook = ''
            # The PyPI wheels (ctranslate2, onnxruntime, av) are manylinux
            # builds that expect a system libstdc++/zlib; /run/opengl-driver
            # holds libcuda.so.1 on NixOS hosts with the NVIDIA driver.
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [
              pkgs.stdenv.cc.cc.lib
              pkgs.zlib
            ]}:/run/opengl-driver/lib''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
          '';
        };
      });

      packages.x86_64-linux =
        let pkgs = nixpkgs.legacyPackages.x86_64-linux;
        in import ./nix/package.nix {
          inherit (pkgs) lib;
          inherit pkgs pyproject-nix uv2nix pyproject-build-systems;
          python = pkgs.python312;
        };

      checks.x86_64-linux.server-smoke =
        let
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
          server = self.packages.x86_64-linux.server;
        in
        pkgs.runCommand "stt-server-smoke" { } ''
          ${server}/bin/stt server --help > /dev/null
          ${server}/bin/python -c "from stt.server import contract, engine, models; contract.load_schema('request'); import jsonschema, av"
          touch $out
        '';
    };
}
