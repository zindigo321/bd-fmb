# Round 1 checklist

Scope: README and 洞一 constellation design problem only. No optimization or seven-day coverage run.

| ID | Check |
|---|---|
| ENV | Repository state, fmb interpreter/dependencies, STK version/COM |
| DATA | Safe selective ZIP extraction, source hashes, script review |
| GEO | Encoding, CRS, order, bounds, geometry/rings/islands, AT correspondence, plot |
| GEN | 28=7x4 Walker Delta, 600 km debug baseline, J2-derived SSO, CSV/config |
| TEST | Determinism, physical residual, normal and invalid inputs, unsupported shapes |
| STK1 | Isolated COM instance, one diagnostic satellite, J2000/J2/30 s, <=2 h |
| STK28 | 28 satellites, finite monotonic actual propagation samples |
| REOPEN | Save all associated files, new instance reopen and compare |
| SENSOR | Optional actual rectangular Sensor Access on a few interior points |
| REPORT | Per-check statuses, commands/exit codes/durations, evidence, reproduction |

Never attach to or clear an existing user scenario. Never run scripts from the archive.
Formal N range is 2..100; N=1 is a diagnostic only. Unequal-plane populations unsupported.
