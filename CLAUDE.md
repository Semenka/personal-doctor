# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Repository Split

This monorepo has been split into two independent projects. Each is available in two forms:

### Local Repositories (ready to use)

| Project | Local Path | Description |
|---|---|---|
| **Personal Doctor** | `/home/user/personal-doctor-python/` | Python FastAPI daily health advisor — Oura Ring, Google Drive, Claude Opus 4.6 AI advisor for sperm motility & energy, email delivery |
| **HackMoney2026** | `/home/user/hackmoney2026/` | Solidity contracts + React dashboard — Dolomite WBTC/USDC position on Arbitrum |

Both are initialized git repos with their own `CLAUDE.md`, `.gitignore`, `README.md`, and an initial commit.

### Orphan Branches (in this repo)

The same content also lives as orphan branches in this repository for easy GitHub repo creation:

| Branch | Content |
|---|---|
| `personal-doctor-python` | Python health advisor — standalone root, no shared history |
| `hackmoney2026` | Solidity + React DeFi dashboard — standalone root, no shared history |

## Creating the GitHub Repositories

### Option A: From local repos

```bash
# 1. Create repos on GitHub (https://github.com/new) — do NOT initialize with README

# 2. Personal Doctor
cd /home/user/personal-doctor-python
git remote add origin git@github.com:Semenka/personal-doctor-python.git
git push -u origin main

# 3. HackMoney2026
cd /home/user/hackmoney2026
git remote add origin git@github.com:Semenka/hackmoney2026.git
git push -u origin main
```

### Option B: From orphan branches (using GitHub CLI)

```bash
# 1. Create repos on GitHub
gh repo create Semenka/personal-doctor-python --public --description "Python FastAPI daily health advisor"
gh repo create Semenka/hackmoney2026 --public --description "Dolomite Guardian — DeFi position monitoring"

# 2. Push orphan branches as main
cd /home/user/personal-doctor
git push git@github.com:Semenka/personal-doctor-python.git personal-doctor-python:main
git push git@github.com:Semenka/hackmoney2026.git hackmoney2026:main
```

## This Repository

The original `personal-doctor` repo served as a monorepo. Project contents have been extracted into the standalone repositories above. This repo is retained for git history only.
