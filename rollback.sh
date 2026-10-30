#!/bin/bash
set -euo pipefail
# Follow the reference project's rollback: stop and remove the container.
bash "$(cd -- "$(dirname -- "$0")" && pwd)/stop.sh"
