# SentraSQL — BUILD_LOG

Technical record generated from the **actual, current state** of the codebase on disk.
This file is maintained by Cline as a standing responsibility: after every completed
task, the relevant section(s) below are updated to match what was actually built.
It is not a plan and not a restatement of prompts — it reflects real files, real
signatures, and real output.

---

## Index

The full technical record now lives in append-only topic files under `docs/build/` (each file begins with an `<!-- APPEND-ONLY -->` header — new dated entries are appended
at the bottom only; existing content above is never edited or deleted).

- [environment.md](docs/build/environment.md) — Python environment, tooling, dependency pins, and repository file structure.
- [schema.md](docs/build/schema.md) — SQLite database schema (`db/schema.sql`) with PRAGMA and CHECK-constraint verification.
- [data_pipeline.md](docs/build/data_pipeline.md) — data transformation, loading, and country-to-timezone mapping layers.
- [graph.md](docs/build/graph.md) — LangGraph data models, node functions, and graph wiring.
- [dashboard_ui.md](docs/build/dashboard_ui.md) — Streamlit dashboard UI layer: design system, landing-page composition, and responsive layout.
- [commits.md](docs/build/commits.md) — full commit history (`git log --oneline`).
