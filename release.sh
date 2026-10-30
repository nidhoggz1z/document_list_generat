#!/bin/bash
set -euo pipefail

app_name=${APP_NAME:-file-retrieval}
version=${VERSION:-2.2}
deploy_dir=${DEPLOY_DIR:-/data/deploy_${app_name}}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

docker load -i "${app_name}-${version}.tar.gz"
mkdir -p "$deploy_dir"
if [ "$script_dir" != "$(cd "$deploy_dir" && pwd)" ]; then
    cp -- start.sh stop.sh show.sh restore.sh rollback.sh DEPLOYMENT.md "$deploy_dir/"
fi
# Keep onsite rules, credentials, task history and logs during code updates.
if [ ! -e "$deploy_dir/list" ]; then
    cp -a -- list "$deploy_dir/list"
fi
if [ ! -e "$deploy_dir/model-api.env" ]; then
    cp -- model-api.env.example "$deploy_dir/model-api.env"
    chmod 600 "$deploy_dir/model-api.env"
    printf 'Fill in %s/model-api.env, then restart with bash start.sh to enable model tasks.\n' "$deploy_dir"
fi
mkdir -p "$deploy_dir/task_storage" "$deploy_dir/log"
export APP_NAME="$app_name" VERSION="$version" DEPLOY_DIR="$deploy_dir"
bash "$deploy_dir/start.sh"
bash "$deploy_dir/show.sh"
