# Development shell: Python 3.14 (required by Home Assistant 2026.10) plus uv.
# Usage:
#   nix-shell
#   uv venv && uv pip install -r requirements_test.txt
#   pytest
{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  packages = [
    pkgs.python314
    pkgs.uv
  ];

  # Binary wheels from PyPI expect these libraries in the usual places.
  LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
    pkgs.stdenv.cc.cc.lib
    pkgs.zlib
  ];

  UV_PYTHON_DOWNLOADS = "never";

  shellHook = ''
    if [ -d .venv ]; then
      source .venv/bin/activate
    fi
  '';
}
