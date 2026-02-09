# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Repository Overview

Multi-project workspace containing two independent applications:

1. **Personal Doctor** (`Personal doctor/`) — Python FastAPI daily health advisor that analyzes sleep, activity, and nutrition data from Oura Ring and research databases to generate personalized recommendations.
2. **HackMoney2026** (`HackMoney2026/`) — Solidity smart contracts + React dashboard to monitor and defend a Dolomite WBTC/USDC position on Arbitrum.

Additional directories:
- `Aura Ring Data/` — Raw Oura ring data logs (JSON)
- `Model HM/`, `Nanochat/`, `My docs/` — Ancillary/empty directories

## Personal Doctor (Python)

### Tech Stack
- Python 3.12, FastAPI 0.115, Uvicorn, Jinja2
- psycopg 3.2 (PostgreSQL, optional — falls back to JSON file storage)
- APScheduler 3.10 (background daily sync)
- pypdf 4.3 (PDF extraction for lab results)
- requests (Oura v2 API, OpenAlex API)

### Commands

```bash
# All commands run from: Personal doctor/

# Activate virtual environment
source venv/bin/activate

# CLI — generate daily report
python -m app.cli --data data/sample_daily.json --goals energy cognition

# Web server
uvicorn app.web:app --reload

# Manual data sync (Oura, blood, urine, annual, research)
python -m app.sync.cli --source oura --date 2026-01-17

# Start scheduler (7am Oura sync, 7:10am research)
python -m app.sync.scheduler
```

### Architecture

```
Personal doctor/app/
├── cli.py              # CLI entry (argparse)
├── web.py              # FastAPI app, 2 routes (/, /report)
├── models.py           # Dataclasses: DailyData, Goals, Recommendation, DailyReport
├── io.py               # JSON loading helpers
├── recommendations.py  # Signal functions + recommendation builders
├── research/           # OpenAlex journal paper integration
│   ├── models.py       # ResearchPaper, ResearchRecommendation
│   ├── openalex.py     # OpenAlex API client
│   └── pipeline.py     # Daily research sync orchestration
├── sync/               # Data ingestion & storage
│   ├── config.py       # SyncConfig, env var loading
│   ├── cli.py          # Sync CLI
│   ├── pipeline.py     # Oura API fetch & transform
│   ├── scheduler.py    # APScheduler background jobs
│   ├── storage.py      # PostgreSQL + JSON file I/O
│   └── connectors/
│       └── oura.py     # Oura Ring v2 API client
└── templates/          # Jinja2 HTML templates
```

**Key patterns:**
- Signal functions (`_signal_sleep`, `_signal_recovery`, `_signal_hydration`, `_signal_mobility`) classify raw metrics into categories
- Recommendations are context-aware based on 4 optimization goals: `energy`, `reproductive_health`, `cognition`, `sport_performance`
- Research integration fetches papers from The Lancet, NEJM, JAMA via OpenAlex with estimated impact percentages

### Environment Variables

- `OURA_ACCESS_TOKEN` — Oura Ring API bearer token
- `DATABASE_URL` — PostgreSQL connection string (optional)
- `HEALTH_TIMEZONE` — Scheduler timezone (default: `Europe/Paris`)
- `HEALTH_DATA_DIR` — JSON storage directory (default: `data/ingested`)
- `OPENALEX_MAILTO` — Email for OpenAlex API rate limiting

### Testing

No automated test suite yet. Test manually via CLI and web UI.

---

## HackMoney2026 (Solidity + React)

See also: `HackMoney2026/CLAUDE.md` for subproject-specific details.

### Tech Stack
- **Contracts:** Solidity 0.8.28, Hardhat 3.1 (ESM), viem, Hardhat Ignition
- **Web:** React 19, TypeScript 5.9, ethers.js 6, Vite (rolldown-vite 7.2)
- **Networks:** Arbitrum One, Arbitrum Sepolia

### Commands

```bash
# Contracts (from HackMoney2026/contracts/)
npx hardhat compile                 # Compile Solidity
npx hardhat test                    # Run all tests (Solidity + Node.js)
npx hardhat test solidity           # Foundry-style tests only
npx hardhat test nodejs             # Node.js native tests only

# Web (from HackMoney2026/web/)
npm run dev       # Vite dev server (localhost:5173)
npm run build     # tsc -b && vite build
npm run lint      # ESLint
```

### Architecture

**Smart Contracts:**
- `DolomitePositionGuardian` — Reads Dolomite `getAccountValues`, computes safety margin in bps, triggers rebalance when margin < threshold
- `PositionRebalancer` — Executes WBTC→USDC swaps via Uniswap V3 `exactInputSingle`
- Deployment via Hardhat Ignition: PositionRebalancer first, then Guardian with rebalancer address

**Web:**
- Single-page React app (`App.tsx`) with MetaMask connection, position display, and manual rebalance trigger

### Key Details

- Hardhat 3 with dual profiles: `default` (no optimizer), `production` (optimizer, 200 runs)
- Dual test frameworks: Foundry-style Solidity tests + Node.js native `node:test` with viem
- WBTC uses 8 decimals, USDC uses 6 decimals
- TypeScript strict mode in both subprojects

### Environment Variables

**Contracts (.env in contracts/):**
- `DOLOMITE_MARGIN_ADDRESS`, `SAFETY_MARGIN_BPS`, `UNISWAP_V3_ROUTER`, `UNISWAP_V3_POOL_FEE`
- `WBTC_ADDRESS`, `USDC_ADDRESS`
- `ARBITRUM_ONE_RPC_URL`, `ARBITRUM_SEPOLIA_RPC_URL`, `ARBITRUM_PRIVATE_KEY`

**Web (.env in web/):**
- `VITE_DOLOMITE_MARGIN`, `VITE_GUARDIAN_CONTRACT`

---

## General Conventions

- **No CI/CD pipelines** — testing and deployment are manual
- **No global linter config** — Python has no enforced style; TypeScript uses strict mode + ESLint in web/
- **No root-level package.json scripts** — navigate into subproject directories to run commands
- **Paths with spaces** — directory names contain spaces (`Personal doctor/`, `Aura Ring Data/`); always quote paths
- **Secrets** — never commit `.env` files, API tokens, or private keys
