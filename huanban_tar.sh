#!/bin/bash
set -euo pipefail

app_name=${APP_NAME:-file-retrieval}
version=${VERSION:-2.2}
[[ "$app_name" =~ ^[a-z0-9][a-z0-9_.-]*$ && "$version" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]] || exit 1
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"
output_dir="$script_dir/dist/${app_name}-${version}"
image_file="${app_name}-${version}.tar.gz"
mkdir -p "$output_dir"
stage=$(mktemp -d "$output_dir/.pack.XXXXXX")
trap 'rm -rf -- "$stage"' EXIT

# Build directly from src; package.py and src.tar are not used.
# No provenance index: deliver a single-platform image for the older target Docker.
docker build --platform linux/amd64 --provenance=false --tag "${app_name}:${version}" --progress=plain "$@" .
if command -v pigz >/dev/null 2>&1; then
    docker save "${app_name}:${version}" | pigz > "$stage/$image_file"
else
    docker save "${app_name}:${version}" | gzip > "$stage/$image_file"
fi

scripts=(release.sh restore.sh rollback.sh show.sh start.sh stop.sh)
cp -- "${scripts[@]}" DEPLOYMENT.md "$stage/"
chmod +x "$stage/"*.sh
cp -- docs/sdd/model-api.env.example "$stage/model-api.env.example"
mkdir "$stage/list"
for document in list/*材料清单.docx; do
    [[ "${document##*/}" == '~$'* ]] || cp -- "$document" "$stage/list/"
done
cp -- list/话术库对应材料.xlsx "$stage/list/"
tar -C "$stage" -cf "$stage/huanban.tar" \
    "$image_file" "${scripts[@]}" DEPLOYMENT.md model-api.env.example list
(
    cd "$stage"
    split -b 1024M -d -a 3 huanban.tar huanban.tar_
    sha256sum huanban.tar huanban.tar_* > SHA256SUMS
)
# Publish only after build, save, archive and split all succeed.
rm -f -- "$output_dir"/huanban.tar_*
mv -- "$stage/huanban.tar" "$stage/"huanban.tar_* "$stage/SHA256SUMS" "$output_dir/"
printf 'Offline package: %s/huanban.tar\n' "$output_dir"
