#!/usr/bin/env bash
cd "$(dirname "$0")"
exec /usr/bin/python3 overlay.py "$@"
