# Videostimulation

Real-time video-speed synchronization for a vehicle test-bench environment. The application uses a reference driving-speed signal, vehicle speed received over CAN, and `mpv` playback control to adapt video playback speed to the target vehicle speed.

## Repository structure

```text
videostimulation/
├── src/                    # Current optimized application
├── legacy/                 # Existing/original implementation for comparison
├── config/                 # CAN database (DBC)
├── scripts/                # Linux / SocketCAN launcher
├── assets/                 # Application icon
├── data/
│   ├── raw_runs/           # Recorded OLD_SYSTEM / NEW_SYSTEM runs
│   ├── validation/         # Section 6 validation/performance tables
│   └── generated_results/  # Runtime CSV output (gitignored)
├── figures/                # Selected experimental and UI figures
├── docs/                   # Additional documentation
├── requirements.txt
└── README.md
```

## Current implementation

The optimized implementation is `src/videostimulation.py`. Compared with the existing implementation, it includes a more extensive GUI, DAT/CSV/TRC input handling, configurable time/speed columns, CAN monitoring, cached playback timing, playback-factor smoothing/rate limiting, and thesis-result logging.

The application accepts MP4 video and reference data in DAT, CSV, or TRC format. TRC wheel-speed messages are decoded through the supplied DBC. The CAN channel defaults to `can0` and can be overridden with `CAN_CHANNEL`.

## Requirements

- Linux with SocketCAN
- Python 3
- Python packages in `requirements.txt`
- `mpv`
- `ffprobe` (used for optional video-duration display)
- `iproute2` / `can-utils` or equivalent SocketCAN setup
- A CAN interface configured for the target test bench

Install Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Install the system-side MPV dependency using the package manager appropriate for the target Linux image.

## Running

From the repository root:

```bash
./scripts/start_program.sh
```

The launcher configures `can0` at 1 Mbit/s, sets the DBC path relative to the repository, creates the runtime-results directory, and starts the GUI.

If the CAN interface is already configured, the Python application can also be started directly:

```bash
python3 src/videostimulation.py
```

Environment variables:

- `CAN_CHANNEL` — CAN interface name; default: `can0`
- `DBC_PATH` — path to the DBC file; default: `config/TSI_VFR.dbc`
- `RESULTS_DIR` — directory for generated test CSV files; default: `data/generated_results/`
- `MPV_SOCKET` — mpv IPC socket; default: `/tmp/mpvsocket`

## Control concept

The application reads the target vehicle speed from CAN message `0x220`, decodes `Whl1_CdVxActl` using the DBC, and converts the value from m/s to km/h. The reference speed is interpolated from the selected input data. The playback factor is then calculated from target/reference speed and constrained by configured operating and safety limits. Low vehicle speed can trigger automatic pause behavior.

## Experimental data

`data/raw_runs/` contains the recorded OLD_SYSTEM and NEW_SYSTEM runs supplied with the project. `data/validation/` contains the processed validation tables used for the reported synchronization, computational-performance, and communication-performance evaluations.

The figures in `figures/` provide the corresponding GUI, CAN-monitoring, synchronization, playback-factor, and performance visualizations.

## Legacy implementation

`legacy/existing_system.py` preserves the supplied existing-system implementation for comparison against the optimized implementation. It should not be treated as the primary runtime entry point.

## DBC

The repository contains the supplied `TSI_VFR.dbc` used by the application for CAN decoding. The application resolves this file through `DBC_PATH` rather than relying on the original machine-specific absolute path.

## Reproducibility notes

The supplied code and measurements are retained as project evidence. Runtime-generated CSV results are deliberately excluded from version control because they are produced during execution. The supplied thesis PDF, signature image, and deployment-specific/private material are not included in this repository package.

## Status

This repository is a cleaned and portable packaging of the supplied Videostimulation project. Hardware-specific CAN configuration and the availability of `mpv` remain prerequisites for full end-to-end execution.
