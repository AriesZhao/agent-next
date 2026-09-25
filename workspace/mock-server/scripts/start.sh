#!/usr/bin/env bash
# Start the mock server with hot-reload.
# Usage: ./scripts/start.sh [--port 9999]
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python -m mock_server.main "$@"
