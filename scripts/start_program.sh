#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Configure SocketCAN (requires root privileges).
# Adjust bitrate to match the target CAN network if required.
sudo ip link set can0 type can bitrate 1000000
sudo ip link set can0 up

export CAN_CHANNEL="${CAN_CHANNEL:-can0}"
export DBC_PATH="${DBC_PATH:-$PROJECT_ROOT/config/TSI_VFR.dbc}"
export RESULTS_DIR="${RESULTS_DIR:-$PROJECT_ROOT/data/generated_results}"

mkdir -p "$RESULTS_DIR"
exec python3 "$PROJECT_ROOT/src/videostimulation.py"
