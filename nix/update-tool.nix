{ writeShellScriptBin, python3, zstd }:
writeShellScriptBin "yeet-os" ''
  export PATH=${zstd}/bin:$PATH
  exec ${python3}/bin/python3 -B ${../installer}/update.py "$@"
''
