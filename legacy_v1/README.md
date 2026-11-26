# Archived v1 code (weighted rule-engine era) - not used

Superseded by the four-sensor Random Forest system (firmware 4.x, backend 2.x). Kept only so nothing
was deleted during the redesign. Nothing here is imported, tested or served. Safe to delete once you
no longer need it for reference.

| Path | Was | Replaced by |
|---|---|---|
| backend/ | FastAPI app with the weighted risk engine (R = 0.40 M + 0.30 R + 0.30 T), advisory ML, Telegram notifier | `backend/` (RF-authoritative ingestion) |
| dashboard/ | polling dashboard | `frontend/` |
| ml/ | 5-feature trainer on the old synthetic CSV, its model and figures | `ml/` (fs-1, 13 features) |
| simulator/, report_tools/ | old-schema simulator, report generator tied to the rule engine | `scripts/simulate_device.py` |
| tests/ | tests of the rule engine | `tests/` |
| docs/ | open questions / placeholder map / dataset notes for the rule engine | `docs/` |
| README_v1.md | previous README | `README.md` |

The old SQLite tables were renamed (not deleted) to `legacy_readings_v1`, `legacy_events_v1` and
`legacy_experiments_v1` inside `backend/data/landslide.db`; a pre-migration backup is in `backend/data/backups/`.
The old 5000-row synthetic CSV is kept at `datasets/synthetic/legacy_v1/`.
