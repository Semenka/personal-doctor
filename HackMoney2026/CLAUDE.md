# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HackMoney2026 – Dolomite Guardian Prototype. A dashboard + Solidity contracts to monitor a Dolomite WBTC long / USDC borrow position and trigger a defensive rebalance when the safety margin breaches a threshold. Targets Arbitrum One and Arbitrum Sepolia.

## Repository Structure

- `contracts/` — Hardhat v3 project (ESM, Solidity 0.8.28)
- `web/` — React 19 + Vite (rolldown-vite) frontend using ethers.js v6

## Commands

### Contracts (`cd contracts`)

```bash
npx hardhat compile                 # compile Solidity
npx hardhat test                    # run all tests (Solidity + Node.js)
npx hardhat test solidity           # Foundry-style Solidity tests only
npx hardhat test nodejs             # Node.js native test runner only
npx hardhat ignition deploy ./ignition/modules/Counter.ts --network arbitrumSepolia
```

### Web (`cd web`)

```bash
npm run dev       # Vite dev server at http://localhost:5173
npm run build     # tsc -b && vite build
npm run lint      # eslint
```

## Architecture

### Contracts

**DolomitePositionGuardian** (`contracts/contracts/Counter.sol`) — owner-controlled guardian that reads `getAccountValues` from Dolomite Margin, computes safety margin in bps `((supply - borrow) * 10000 / borrow)`, and calls the rebalancer when margin drops below the threshold.

**PositionRebalancer** (`contracts/contracts/PositionRebalancer.sol`) — executes WBTC→USDC swaps via Uniswap V3 `exactInputSingle`. Called by the guardian's `adjustPosition`.

**Deployment** uses Hardhat Ignition (`ignition/modules/Counter.ts`). Deploys PositionRebalancer first, then DolomitePositionGuardian with the rebalancer address.

### Web

Single-page React app (`web/src/App.tsx`) that connects MetaMask, reads position data from Dolomite Margin contract, displays safety margin metrics, and sends `adjustPosition` transactions to the guardian.

## Environment Variables

### Contracts (`.env` in contracts/)
- `DOLOMITE_MARGIN_ADDRESS`, `SAFETY_MARGIN_BPS`, `UNISWAP_V3_ROUTER`, `UNISWAP_V3_POOL_FEE`, `WBTC_ADDRESS`, `USDC_ADDRESS`
- `ARBITRUM_ONE_RPC_URL`, `ARBITRUM_SEPOLIA_RPC_URL`, `ARBITRUM_PRIVATE_KEY`

### Web (`.env` in web/)
- `VITE_DOLOMITE_MARGIN` — Dolomite Margin contract address
- `VITE_GUARDIAN_CONTRACT` — deployed DolomitePositionGuardian address

## Key Details

- Hardhat v3 with dual Solidity profiles: `default` (no optimizer) and `production` (optimizer, 200 runs)
- Tests use both Foundry-style (`Counter.t.sol`) and Node.js native test runner (`test/Counter.ts`) with viem
- Web uses `npm:rolldown-vite@7.2.5` as a Vite override
- WBTC uses 8 decimals, USDC uses 6 decimals
- TypeScript strict mode enabled in both subprojects
