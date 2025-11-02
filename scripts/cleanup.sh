#!/bin/bash

# Cleanup old workspaces and artifacts

set -e

STORAGE_ROOT="${STORAGE_ROOT:-./storage}"
TTL_HOURS="${WORKSPACE_TTL_HOURS:-24}"

echo "Cleaning up old workspaces and artifacts..."
echo "Storage root: $STORAGE_ROOT"
echo "TTL: ${TTL_HOURS} hours"

# Find and remove old workspaces
if [ -d "$STORAGE_ROOT/workspaces" ]; then
    find "$STORAGE_ROOT/workspaces" -mindepth 1 -maxdepth 1 -type d -mtime +$((TTL_HOURS/24)) -exec rm -rf {} \;
    echo "Cleaned up old workspaces"
fi

# Find and remove old artifacts
if [ -d "$STORAGE_ROOT/artifacts" ]; then
    find "$STORAGE_ROOT/artifacts" -type f -mtime +$((TTL_HOURS/24)) -delete
    echo "Cleaned up old artifacts"
fi

echo "Cleanup complete"
