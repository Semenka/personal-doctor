# HackMoney2026 – Dolomite Guardian Prototype

Prototype dashboard + Solidity contract to monitor a Dolomite WBTC long / USDC borrow position and trigger a defensive rebalance when the safety margin breaches a threshold.

## Structure

- `contracts/` Hardhat v3 project with `DolomitePositionGuardian` contract.
- `web/` React + Vite UI that reads Dolomite position data and calls the guardian.

## Prerequisites

- Node.js **22 LTS**
- MetaMask wallet

## Environment variables

Create `web/.env`:

```
VITE_DOLOMITE_MARGIN=0x0000000000000000000000000000000000000000
VITE_GUARDIAN_CONTRACT=0x0000000000000000000000000000000000000000
```

- `VITE_DOLOMITE_MARGIN`: Dolomite Margin contract for Arbitrum One/Sepolia.
- `VITE_GUARDIAN_CONTRACT`: Deployed `DolomitePositionGuardian` address.

## Contracts

Install deps and compile:

```
cd contracts
npm install
npx hardhat compile
```

Deploy (example with Hardhat HTTP networks):

```
npx hardhat ignition deploy ./ignition/modules/Counter.ts --network arbitrumSepolia
```

Ignition module uses (Arbitrum One defaults shown; replace for Sepolia):

```
DOLOMITE_MARGIN_ADDRESS=0x6Bd780E7fDf01D77e4d475c821f1e7AE05409072
SAFETY_MARGIN_BPS=1000
UNISWAP_V3_ROUTER=0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45
UNISWAP_V3_POOL_FEE=3000
WBTC_ADDRESS=0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f
USDC_ADDRESS=0xaf88d065e77c8cC2239327C5EDb3A432268e5831
```

Set RPC + private key variables:

```
ARBITRUM_ONE_RPC_URL=
ARBITRUM_SEPOLIA_RPC_URL=
ARBITRUM_PRIVATE_KEY=
```

Constructor params:
- `dolomiteMarginAddress`
- `rebalancerAddress` (defaults to placeholder `PositionRebalancer`)
- `initialSafetyMarginBps` (e.g. 500 = 5%, 1000 = 10%, 2000 = 20%)

## Web UI

Install and run:

```
cd web
npm install
npm run dev
```

Open http://localhost:5173

### Usage

1. Connect MetaMask and switch to **Arbitrum One** or **Arbitrum Sepolia**.
2. Enter `accountOwner` + `accountNumber` to read the position from Dolomite.
3. Provide WBTC amount + minimum USDC output and press **Execute adjustment**.

## Notes

- The guardian only triggers the rebalance through `IPositionRebalancer`. You still need to implement and deploy a rebalancer contract that performs the actual WBTC→USDC conversion.
- UI reads `getAccountValues` directly from Dolomite to compute the safety margin.
