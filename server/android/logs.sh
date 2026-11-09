#!/bin/bash
# Watch the Python side of the app.
set -euo pipefail
adb logcat -v time python:D DittobufferMobile:D AndroidRuntime:E '*:S'
