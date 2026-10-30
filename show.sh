#!/bin/bash
set -euo pipefail
app_name=${APP_NAME:-file-retrieval}
docker ps -a --filter "name=^/${app_name}$"
docker logs --tail 200 "$app_name"
