#!/bin/sh
set -eu
cd /home/app
exec uvicorn main:app --port 8000 --workers 1 --host 0.0.0.0 --log-level info
