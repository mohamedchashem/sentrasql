<!-- APPEND-ONLY: add new dated entries at the bottom. Never edit or delete existing content above. -->

## 1. Environment

| Item | Value |
| --- | --- |
| Python interpreter (read from `.venv`) | `Python 3.12.14` (`.venv\Scripts\python.exe --version`) |
| Virtual environment location | `.venv` (project root) |
| Environment tooling | **uv** (`uv 0.12.9`, installed at `C:\Users\DELL\.local\bin\uv.exe`) |
| Setup method | Environment created and managed **exclusively with uv** (`uv venv --python 3.12`, `uv pip install`, `uv pip freeze`) — **not** `pip`/`venv`. |
| Environment history | Originally created on CPython 3.14.5; `.venv` was later deleted and recreated pinned to CPython 3.12.14 via `uv venv --python 3.12`, then all dependencies reinstalled from `requirements.txt`. |

### `requirements.txt` (exact verbatim contents)

```text
annotated-types==0.8.0
anyio==4.15.0
certifi==2026.7.22
charset-normalizer==3.5.1
distro==1.9.0
et-xmlfile==2.0.0
h11==0.16.0
httpcore==1.0.9
httpcore2==2.12.0
httpx==0.28.1
httpx2==2.12.0
idna==3.19
jsonpatch==1.33
jsonpointer==3.1.1
langchain==1.3.18
langchain-core==1.6.1
langchain-protocol==0.0.19
langgraph==1.2.11
langgraph-checkpoint==4.2.0
langgraph-prebuilt==1.1.0
langgraph-sdk==0.4.4
langsmith==0.12.1
numpy==2.5.2
openpyxl==3.1.5
orjson==3.12.0
ormsgpack==1.12.2
packaging==26.3
pandas==3.0.5
pycountry==26.2.16
pydantic==2.13.5
pydantic-core==2.46.5
python-dateutil==2.9.0.post0
pytz==2026.3.post1
pyyaml==6.0.3
requests==2.34.2
requests-toolbelt==1.0.0
six==1.17.0
sniffio==1.3.1
tenacity==9.1.4
truststore==0.10.4
typing-extensions==4.16.0
typing-inspection==0.4.4
tzdata==2026.3
urllib3==2.7.0
uuid-utils==0.17.0
websockets==16.1.1
xxhash==4.0.1
zstandard==0.25.0
```

> Note: `pycountry==26.2.16` and `pytz==2026.3.post1` were added via
> `uv pip install` for the country-to-timezone layer (see section 9).

---

## 2. File Structure

Directory tree as it actually exists on disk, excluding `.venv`, `__pycache__`, and `.git`.

```text
.
├── .env                     # empty env file (gitignored; placeholder for real secrets)
├── .env.example             # DEEPSEEK_API_KEY=your_key_here
├── .gitignore               # .env, .venv/, __pycache__/, *.pyc, data/raw/* & data/processed/* (!their .gitkeep files)
├── BUILD_LOG.md             # this file
├── DESIGN_LOG.md            # project working log; present on disk, currently UNTRACKED
├── README.md                # "# SentraSQL" + tagline
├── requirements.txt         # pinned dependency list (see Environment section)
├── app/                     # empty placeholder folder (not git-tracked)
├── data/
│   ├── processed/           # processed/derived data (gitignored, except .gitkeep)
│   │   ├── .gitkeep
│   │   └── sentrasql.db     # SQLite DB — transactions populated with 1,067,354 rows; created from db/schema.sql (see sections 6 and 8)
│   └── raw/                 # raw dataset input folder
│       ├── .gitkeep
│       └── online_retail_II.csv   # ~94.8 MB raw dataset (~1,067,371 rows), gitignored
├── db/                      # database layer — DDL + pure transform + loader + timezone map
│   ├── __init__.py          # empty package marker (db is an importable package)
│   ├── load.py              # schema-ready DataFrame → transactions table (see section 8)
│   ├── schema.sql           # SQLite DDL: transactions + country_timezones (see section 6)
│   ├── timezones.py         # pycountry/pytz country → IANA timezone map (see section 9)
│   └── transform.py         # raw DataFrame → schema-ready DataFrame (see section 7)
├── graph/                   # LangGraph pipeline package
│   ├── build.py             # graph construction/wiring + module-level compiled graph
│   ├── nodes.py             # eight node stubs (no logic yet)
│   └── state.py             # Pydantic data models (state schema)
├── reports/                 # generated Markdown reports
│   └── data_profile.md      # output of scripts/profile_dataset.py
├── scripts/                 # reusable utility / profiling scripts
│   ├── profile_anomalies.py # console anomaly scan (Quantity/Price/Invoice/StockCode)
│   ├── profile_dataset.py   # dataset profiling → reports/data_profile.md
│   ├── profile_stockcodes.py# StockCode-structure anomaly scan (console)
│   ├── run_load.py          # load dataset into transactions + verification queries (see section 8)
│   ├── run_load_timezones.py# load pycountry/pytz country → timezone map (see section 9)
│   └── run_transform_check.py# smoke-test of db/transform.py → verification report (see section 7)
└── tests/                   # empty placeholder folder (not git-tracked)
```

Folder purposes:

| Folder | Purpose |
| --- | --- |
| `data/` | Holds **only data files**. `raw/` = unmodified source data; `processed/` = cleaned/derived data (currently holds the populated `sentrasql.db`). |
| `graph/` | Core application code for the LangGraph pipeline: state schema, node functions, and graph wiring. |
| `scripts/` | Reusable utility, data-profiling, and verification scripts (separate from application code); `run_transform_check.py` smoke-tests `db/transform.py`, and `run_load.py` / `run_load_timezones.py` load + verify the populated DB. |
| `app/` | Reserved for user-facing application code (currently empty). |
| `db/` | Database layer — `schema.sql` (SQLite DDL for `transactions` + `country_timezones`), `transform.py` (raw CSV → schema-ready DataFrame), `load.py` (schema-ready DataFrame → `transactions` rows), `timezones.py` (pycountry/pytz country → timezone map), and the package init marker. |
| `tests/` | Reserved for tests (currently empty). |
| `reports/` | Generated analysis output (currently holds the dataset profile). |

> Note: `app/` and `tests/` exist on disk but contain no files, so git does not
> track them (git ignores empty directories). `data/raw/*` and `data/processed/*`
> are gitignored with exceptions for their `.gitkeep` files — so the raw CSV and
> the derived `data/processed/sentrasql.db` are present on disk but not tracked.

---

