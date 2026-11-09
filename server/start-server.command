#!/bin/bash
# Double-click this file in Finder to start the family server on macOS.
cd "$(dirname "$0")" || exit 1
exec ./start-server.sh
