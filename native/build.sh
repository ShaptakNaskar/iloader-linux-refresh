#!/usr/bin/env bash
set -euo pipefail

ilr_project_dir="$(cd "$(dirname "$0")/.." && pwd)"
ilr_native_version=0.5.6-ilr.1
ilr_upstream_commit=50dc10e25485e508e89d1114f7413da50587d343
ilr_build_dir="$(mktemp -d)"
trap 'rm -rf -- "$ilr_build_dir"' EXIT

git clone --filter=blob:none --no-checkout https://github.com/BartolomeoRusso9/altkeeper.git "$ilr_build_dir/upstream"
git -C "$ilr_build_dir/upstream" fetch --depth 1 origin "$ilr_upstream_commit"
git -C "$ilr_build_dir/upstream" checkout --detach FETCH_HEAD
git -C "$ilr_build_dir/upstream" apply --check "$ilr_project_dir/native/fail-closed.patch"
git -C "$ilr_build_dir/upstream" apply "$ilr_project_dir/native/fail-closed.patch"

cd "$ilr_build_dir/upstream"
cargo test --locked --release --jobs 2 --bin altkeeper
cargo build --locked --release --jobs 2 --bin altkeeper
test "$(target/release/altkeeper --version)" = "altkeeper $ilr_native_version"

ilr_package="altkeeper-$ilr_native_version-linux-x86_64"
mkdir -p "$ilr_build_dir/$ilr_package" "$ilr_project_dir/dist"
cp target/release/altkeeper "$ilr_build_dir/$ilr_package/altkeeper-ilr"
cp LICENSE "$ilr_build_dir/$ilr_package/LICENSE.upstream"
cp THIRD-PARTY-NOTICES.md "$ilr_build_dir/$ilr_package/THIRD-PARTY-NOTICES.md"
cp vendor/isideload/LICENSE "$ilr_build_dir/$ilr_package/LICENSE.isideload"
cp "$ilr_project_dir/LICENSE" "$ilr_build_dir/$ilr_package/LICENSE.integration"
cp "$ilr_project_dir/native/fail-closed.patch" "$ilr_build_dir/$ilr_package/"
cp "$ilr_project_dir/native/UPSTREAM.md" "$ilr_build_dir/$ilr_package/"
rustc --version > "$ilr_build_dir/$ilr_package/RUSTC.txt"
tar -czf "$ilr_project_dir/dist/$ilr_package.tar.gz" -C "$ilr_build_dir" "$ilr_package"
cd "$ilr_project_dir/dist"
sha256sum "$ilr_package.tar.gz" > "$ilr_package.tar.gz.sha256"
