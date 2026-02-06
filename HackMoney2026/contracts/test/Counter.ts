import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { network } from "hardhat";

describe("DolomitePositionGuardian", async function () {
  const { viem } = await network.connect();

  // Placeholder addresses for constructor args
  const dolomiteMargin = "0x0000000000000000000000000000000000001234";
  const swapRouter = "0x000000000000000000000000000000000000ABcD";
  const wbtcAddr = "0x000000000000000000000000000000000000BbBB";
  const usdcAddr = "0x000000000000000000000000000000000000CcCc";
  const poolFee = 3000;
  const safetyMarginBps = 1000n;

  it("Should deploy and set the correct owner", async function () {
    const rebalancer = await viem.deployContract("PositionRebalancer", [
      swapRouter,
      wbtcAddr,
      usdcAddr,
      poolFee,
    ]);

    const guardian = await viem.deployContract("DolomitePositionGuardian", [
      dolomiteMargin,
      rebalancer.address,
      safetyMarginBps,
    ]);

    const [deployer] = await viem.getWalletClients();
    const ownerAddr = await guardian.read.owner();

    assert.equal(
      ownerAddr.toLowerCase(),
      deployer.account.address.toLowerCase(),
    );
  });

  it("Should store the initial safety margin", async function () {
    const rebalancer = await viem.deployContract("PositionRebalancer", [
      swapRouter,
      wbtcAddr,
      usdcAddr,
      poolFee,
    ]);

    const guardian = await viem.deployContract("DolomitePositionGuardian", [
      dolomiteMargin,
      rebalancer.address,
      safetyMarginBps,
    ]);

    const margin = await guardian.read.safetyMarginBps();
    assert.equal(margin, safetyMarginBps);
  });
});
