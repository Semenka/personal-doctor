# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Repository Split Notice

This repository has been split into two independent repositories:

| Project | Location | Description |
|---|---|---|
| **Personal Doctor** | `/home/user/personal-doctor-python/` | Python FastAPI daily health advisor — Oura Ring integration, research-backed recommendations |
| **HackMoney2026** | `/home/user/hackmoney2026/` | Solidity smart contracts + React dashboard — Dolomite WBTC/USDC position monitoring on Arbitrum |

Each repository has its own `CLAUDE.md` with full project documentation, `.gitignore`, and `README.md`.

## Setting Up GitHub Remotes

The new repositories are initialized locally. To push them to GitHub:

```bash
# Personal Doctor
cd /home/user/personal-doctor-python
git remote add origin git@github.com:Semenka/personal-doctor-python.git
git push -u origin main

# HackMoney2026
cd /home/user/hackmoney2026
git remote add origin git@github.com:Semenka/hackmoney2026.git
git push -u origin main
```

Create the GitHub repositories first via https://github.com/new before pushing.

## This Repository

This original `personal-doctor` repo served as a monorepo. The project contents have been moved to the standalone repositories listed above. This repo is retained for history only.
