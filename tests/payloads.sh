#!/usr/bin/env bash
# Run from a workstation against a disposable, enrolled Yeet OS host.
set -euo pipefail
: "${CATCH_HOST:?set CATCH_HOST to the test host}"
cli=${YEET_CLI:-yeet}
work=$(mktemp -d)
name="ytest-$(date +%s)-$RANDOM"
cleanup() {
  "$cli" rm --clean -y "$name" >/dev/null || echo "Remove test service $name on $CATCH_HOST" >&2
  rm -rf "$work"
}
trap cleanup EXIT
cd "$work"

make_payload() {
  cat > "$1" <<SCRIPT
#!/usr/bin/env bash
set -eu
echo "$2:\$(id -un)" > "\$HOME/proof"
exec sleep 86400
SCRIPT
  chmod +x "$1"
}

expect_payload() {
  local expected="$1:yeet-svc"
  for ((i=0; i<20; i++)); do
    if [[ $("$cli" --no-tty ssh "$name" -- cat proof) == "$expected" ]]; then return; fi
    sleep 1
  done
  echo "Expected $expected from $name" >&2
  return 1
}

make_payload first.sh first
make_payload second.sh second
"$cli" run "$name" ./first.sh
expect_payload first
"$cli" stage "$name" ./second.sh
expect_payload first
"$cli" stage "$name" commit
expect_payload second
"$cli" service rollback "$name"
expect_payload first
"$cli" copy first.sh "$name:data/copy.sh"
"$cli" copy "$name:data/copy.sh" copy.sh
cmp first.sh copy.sh
"$cli" restart "$name"
expect_payload first
