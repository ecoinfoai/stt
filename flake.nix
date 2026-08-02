{
  description = "stt — local lecture transcription pipeline (yt-dlp + faster-whisper)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
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
    };
}
