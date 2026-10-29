#!/bin/sh
set -eu
exec timeout --signal=TERM --kill-after=1s 14s python -m tools.green_exact_smoke "$@"
