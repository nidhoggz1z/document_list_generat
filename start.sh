#!/bin/bash
set -euo pipefail

app_name=${APP_NAME:-file-retrieval}
version=${VERSION:-2.2}
deploy_dir=${DEPLOY_DIR:-/data/deploy_${app_name}}
api_port=${API_PORT:-18008}

# Validate inputs and image before removing an existing container.
if [ ! -f "$deploy_dir/model-api.env" ] || [ ! -d "$deploy_dir/list" ]; then
    printf 'Missing model-api.env or list under %s; run release.sh first.\n' "$deploy_dir" >&2
    exit 1
fi
docker image inspect "${app_name}:${version}" >/dev/null
mkdir -p "$deploy_dir/task_storage" "$deploy_dir/log"
if docker container inspect "$app_name" >/dev/null 2>&1; then
    docker rm -f "$app_name"
fi
docker run -d \
    --name "$app_name" \
    -p "${api_port}:8000" \
    -e DOCUMENT_DIR=/home/app/list \
    -e TASK_STORAGE_ROOT=/home/app/task_storage \
    --mount "type=bind,source=$deploy_dir/model-api.env,target=/home/app/model-api.env,readonly" \
    --mount "type=bind,source=$deploy_dir/list,target=/home/app/list,readonly" \
    --mount "type=bind,source=$deploy_dir/task_storage,target=/home/app/task_storage" \
    --mount "type=bind,source=$deploy_dir/log,target=/home/app/log" \
    --restart unless-stopped \
    --log-driver json-file --log-opt max-size=1g --log-opt max-file=3 \
    "${app_name}:${version}"
