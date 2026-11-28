# Publishing a Yeet release

`yeet/VERSION` supplies the release version for both the OS and the workstation CLI. Update it and the changelog in `yeet/website/docs`, test the changes, and land the commit on main. Create and push an annotated tag matching the version, prefixed with `v`.

Build these files from that exact commit in a clean checkout. Run OS commands from the repository root and Go development commands under `yeet/`. The existing Go module name is retained for source compatibility.

- On x86-64 Linux, run `nix build .#installer` and copy `result/iso/yeet-os-installer.iso` into the release directory. Generate `SHA256SUMS` there with `sha256sum yeet-os-installer.iso > SHA256SUMS`.
- On x86-64 and ARM64 Linux, run `tools/build-update /path/to/new-directory` and collect the four files from each builder.
- With the Go version from `yeet/go.mod`, run `tools/build-cli /path/to/release-directory`. It builds the Mac and Linux CLI for Intel/AMD and ARM, their checksums and the installer script.

Run `tools/publish-release /path/to/release-directory /path/to/notes.md` from the tagged checkout. It checks the 19 files, uploads them to a draft in `yeetrun/yeet-os`, and compares the GitHub digests before marking the release latest. Pass `--check` to verify the local files alone. Existing hosts read their architecture's manifest from this release. Workstations use the same release for `yeet upgrade`. The nightly workflow publishes workstation previews to a separate prerelease.
