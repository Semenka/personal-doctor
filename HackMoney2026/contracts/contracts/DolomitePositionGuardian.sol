// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface IDolomiteMargin {
  function getAccountValues(
    address accountOwner,
    uint256 accountNumber
  ) external view returns (uint256 supplyValue, uint256 borrowValue);
}

interface IPositionRebalancer {
  function rebalance(
    address accountOwner,
    uint256 accountNumber,
    uint256 wbtcAmountIn,
    uint256 minUsdcOut
  ) external;
}

contract DolomitePositionGuardian {
  address public owner;
  IDolomiteMargin public dolomiteMargin;
  IPositionRebalancer public rebalancer;

  uint256 public safetyMarginBps;
  uint256 public constant MAX_BPS = 10_000;

  event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
  event SafetyMarginUpdated(uint256 previousBps, uint256 newBps);
  event RebalancerUpdated(address indexed previousRebalancer, address indexed newRebalancer);
  event DolomiteMarginUpdated(address indexed previousMargin, address indexed newMargin);
  event PositionAdjusted(
    address indexed accountOwner,
    uint256 indexed accountNumber,
    uint256 marginBps,
    uint256 wbtcAmountIn,
    uint256 minUsdcOut
  );

  error NotOwner();
  error InvalidAddress();
  error InvalidBps();
  error NoBorrow();
  error SafetyMarginNotBreached(uint256 marginBps, uint256 thresholdBps);

  modifier onlyOwner() {
    if (msg.sender != owner) {
      revert NotOwner();
    }
    _;
  }

  constructor(address dolomiteMarginAddress, address rebalancerAddress, uint256 initialSafetyMarginBps) {
    if (dolomiteMarginAddress == address(0) || rebalancerAddress == address(0)) {
      revert InvalidAddress();
    }
    if (initialSafetyMarginBps > MAX_BPS) {
      revert InvalidBps();
    }
    owner = msg.sender;
    dolomiteMargin = IDolomiteMargin(dolomiteMarginAddress);
    rebalancer = IPositionRebalancer(rebalancerAddress);
    safetyMarginBps = initialSafetyMarginBps;
  }

  function transferOwnership(address newOwner) external onlyOwner {
    if (newOwner == address(0)) {
      revert InvalidAddress();
    }
    emit OwnershipTransferred(owner, newOwner);
    owner = newOwner;
  }

  function setSafetyMarginBps(uint256 newSafetyMarginBps) external onlyOwner {
    if (newSafetyMarginBps > MAX_BPS) {
      revert InvalidBps();
    }
    emit SafetyMarginUpdated(safetyMarginBps, newSafetyMarginBps);
    safetyMarginBps = newSafetyMarginBps;
  }

  function setDolomiteMargin(address newDolomiteMargin) external onlyOwner {
    if (newDolomiteMargin == address(0)) {
      revert InvalidAddress();
    }
    emit DolomiteMarginUpdated(address(dolomiteMargin), newDolomiteMargin);
    dolomiteMargin = IDolomiteMargin(newDolomiteMargin);
  }

  function setRebalancer(address newRebalancer) external onlyOwner {
    if (newRebalancer == address(0)) {
      revert InvalidAddress();
    }
    emit RebalancerUpdated(address(rebalancer), newRebalancer);
    rebalancer = IPositionRebalancer(newRebalancer);
  }

  function getSafetyMarginBps(
    address accountOwner,
    uint256 accountNumber
  ) public view returns (uint256 marginBps, uint256 supplyValue, uint256 borrowValue) {
    (supplyValue, borrowValue) = dolomiteMargin.getAccountValues(accountOwner, accountNumber);
    if (borrowValue == 0) {
      revert NoBorrow();
    }
    if (supplyValue <= borrowValue) {
      marginBps = 0;
    } else {
      marginBps = ((supplyValue - borrowValue) * MAX_BPS) / borrowValue;
    }
  }

  function adjustPosition(
    address accountOwner,
    uint256 accountNumber,
    uint256 wbtcAmountIn,
    uint256 minUsdcOut
  ) external {
    (uint256 marginBps,,) = getSafetyMarginBps(accountOwner, accountNumber);
    if (marginBps > safetyMarginBps) {
      revert SafetyMarginNotBreached(marginBps, safetyMarginBps);
    }

    rebalancer.rebalance(accountOwner, accountNumber, wbtcAmountIn, minUsdcOut);
    emit PositionAdjusted(accountOwner, accountNumber, marginBps, wbtcAmountIn, minUsdcOut);
  }
}
