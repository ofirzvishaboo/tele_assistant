#!/bin/bash
set -e

# Fix permissions for mounted volumes (run as root)
# This ensures the botuser can write to token.json and database directory

if [ -f /app/token.json ]; then
    chown -f botuser:botuser /app/token.json 2>/dev/null || true
    chmod -f 600 /app/token.json 2>/dev/null || true
fi

if [ -d /app/src/db ]; then
    chown -Rf botuser:botuser /app/src/db 2>/dev/null || true
    chmod -Rf 755 /app/src/db 2>/dev/null || true
fi

# Switch to botuser and execute the main command
exec gosu botuser "$@"

