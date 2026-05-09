#!/bin/bash
set -a
source /Users/stephencummins/secrets/api-keys/n8n.env
set +a
exec /Users/stephencummins/taskmanager/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 3125 --app-dir /Users/stephencummins/taskmanager
