#!/bin/bash
set -euo pipefail
bash "$(cd -- "$(dirname -- "$0")" && pwd)/stop.sh"
