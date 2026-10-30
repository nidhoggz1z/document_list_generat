#!/bin/bash
set -euo pipefail
docker rm -f "${APP_NAME:-file-retrieval}"
echo 'Container stopped and removed; mounted data retained.'
