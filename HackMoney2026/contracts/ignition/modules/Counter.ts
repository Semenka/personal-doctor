import { buildModule } from "@nomicfoundation/hardhat-ignition/modules";

const dolomiteMargin = process.env.DOLOMITE_MARGIN_ADDRESS ?? "0x0000000000000000000000000000000000000000";
const safetyMarginBps = BigInt(process.env.SAFETY_MARGIN_BPS ?? "1000");
const uniswapRouter = process.env.UNISWAP_V3_ROUTER ?? "0x0000000000000000000000000000000000000000";
const wbtcAddress = process.env.WBTC_ADDRESS ?? "0x0000000000000000000000000000000000000000";
const usdcAddress = process.env.USDC_ADDRESS ?? "0x0000000000000000000000000000000000000000";
const poolFee = BigInt(process.env.UNISWAP_V3_POOL_FEE ?? "3000");

export default buildModule("GuardianModule", (m) => {
  const rebalancer = m.contract("PositionRebalancer", [uniswapRouter, wbtcAddress, usdcAddress, poolFee]);
  const guardian = m.contract("DolomitePositionGuardian", [dolomiteMargin, rebalancer, safetyMarginBps]);

  return { guardian, rebalancer };
});
