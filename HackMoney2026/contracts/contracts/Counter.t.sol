// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {DolomitePositionGuardian} from "./DolomitePositionGuardian.sol";
import {PositionRebalancer} from "./PositionRebalancer.sol";
import {Test} from "forge-std/Test.sol";

contract GuardianTest is Test {
  DolomitePositionGuardian guardian;

  function setUp() public {
    PositionRebalancer rebalancer = new PositionRebalancer(
      address(0xABCD),  // swapRouter placeholder
      address(0xBBBB),  // wbtc placeholder
      address(0xCCCC),  // usdc placeholder
      3000              // poolFee
    );
    guardian = new DolomitePositionGuardian(address(0x1234), address(rebalancer), 1000);
  }

  function testOwnerIsDeployer() public view {
    require(guardian.owner() == address(this), "owner should be deployer");
  }
}
