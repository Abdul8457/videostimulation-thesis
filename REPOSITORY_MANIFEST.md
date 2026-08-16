# Repository manifest

## Source-to-repository mapping

| Supplied material | Repository location |
|---|---|
| Optimized code.py | `src/videostimulation.py` |
| Exisiting system code.py | `legacy/existing_system.py` |
| TSI_VFR (2)(3).dbc / TSI_VFR (2)(4).dbc | `config/TSI_VFR.dbc` (duplicates were byte-identical) |
| start_program.sh | `scripts/start_program.sh` |
| Videostimulation_ICON.png | `assets/Videostimulation_ICON.png` |
| OLD_SYSTEM_* / NEW_SYSTEM_* CSVs | `data/raw_runs/` |
| Section 6 validation CSVs | `data/validation/` |
| Supplied GUI/performance/setup figures | `figures/` |

## Excluded from the repository package

- `Abdul_Final.pdf`
- `signature.png`
- `SSH deployment(1).jpeg`
- duplicate UUID-named screenshots where a named equivalent was supplied

These were excluded to keep the software repository focused and to avoid placing thesis/signature/deployment-private material into version control.
