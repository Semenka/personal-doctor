// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

interface IPositionRebalancer {
  function rebalance(
    address accountOwner,
    uint256 accountNumber,
    uint256 wbtcAmountIn,
    uint256 minUsdcOut
  ) external;
}

interface IERC20 {
  function transferFrom(address from, address to, uint256 amount) external returns (bool);
  function approve(address spender, uint256 amount) external returns (bool);
}

interface ISwapRouter {
  struct ExactInputSingleParams {
    address tokenIn;
    address tokenOut;
    uint24 fee;
    address recipient;
    uint256 deadline;
    uint256 amountIn;
    uint256 amountOutMinimum;
    uint160 sqrtPriceLimitX96;
  }

  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut);
}

contract PositionRebalancer is IPositionRebalancer {
  ISwapRouter public immutable swapRouter;
  IERC20 public immutable wbtc;
  IERC20 public immutable usdc;
  uint24 public immutable poolFee;

  event Rebalanced(
    address indexed accountOwner,
    uint256 indexed accountNumber,
    uint256 wbtcAmountIn,
    uint256 minUsdcOut,
    uint256 usdcReceived
  );

  error InvalidAddress();

  constructor(address swapRouterAddress, address wbtcAddress, address usdcAddress, uint24 poolFee_) {
    if (swapRouterAddress == address(0) || wbtcAddress == address(0) || usdcAddress == address(0)) {
      revert InvalidAddress();
    }
    swapRouter = ISwapRouter(swapRouterAddress);
    wbtc = IERC20(wbtcAddress);
    usdc = IERC20(usdcAddress);
    poolFee = poolFee_;
  }

  function rebalance(
    address accountOwner,
    uint256 accountNumber,
    uint256 wbtcAmountIn,
    uint256 minUsdcOut
  ) external override {
    require(wbtcAmountIn > 0, "wbtcAmountIn=0");

    wbtc.transferFrom(msg.sender, address(this), wbtcAmountIn);
    wbtc.approve(address(swapRouter), wbtcAmountIn);

    uint256 usdcReceived = swapRouter.exactInputSingle(
      ISwapRouter.ExactInputSingleParams({
        tokenIn: address(wbtc),
        tokenOut: address(usdc),
        fee: poolFee,
        recipient: accountOwner,
        deadline: block.timestamp,
        amountIn: wbtcAmountIn,
        amountOutMinimum: minUsdcOut,
        sqrtPriceLimitX96: 0
      })
    );

    emit Rebalanced(accountOwner, accountNumber, wbtcAmountIn, minUsdcOut, usdcReceived);
  }
}
