#!/bin/sh
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$PACKAGE_ROOT/pi/install_on_pi.sh" "$@"
