import { useEffect, useMemo, useState } from 'react'
import { BrowserProvider, Contract, ethers } from 'ethers'
import './App.css'

const DOL_MARGIN_ABI = [
  'function getAccountValues(address accountOwner,uint256 accountNumber) view returns (uint256 supplyValue,uint256 borrowValue)',
]

const GUARDIAN_ABI = [
  'function safetyMarginBps() view returns (uint256)',
  'function adjustPosition(address accountOwner,uint256 accountNumber,uint256 wbtcAmountIn,uint256 minUsdcOut)',
]

const NETWORKS: Record<string, { label: string; chainId: number }> = {
  arbitrum: { label: 'Arbitrum One', chainId: 42161 },
  sepolia: { label: 'Arbitrum Sepolia', chainId: 421614 },
}

const NETWORK_BY_CHAIN_ID: Record<number, string> = Object.entries(NETWORKS).reduce(
  (acc, [key, value]) => {
    acc[value.chainId] = key
    return acc
  },
  {} as Record<number, string>,
)

function App() {
  const [walletAddress, setWalletAddress] = useState<string>('')
  const [chainId, setChainId] = useState<number | null>(null)
  const [selectedNetwork, setSelectedNetwork] = useState('arbitrum')

  const [accountOwner, setAccountOwner] = useState<string>('')
  const [accountNumber, setAccountNumber] = useState('0')
  const [safetyMargin, setSafetyMargin] = useState<number | null>(null)
  const [supplyValue, setSupplyValue] = useState<string>('')
  const [borrowValue, setBorrowValue] = useState<string>('')
  const [computedMargin, setComputedMargin] = useState<string>('')

  const [wbtcAmountIn, setWbtcAmountIn] = useState('0.01')
  const [minUsdcOut, setMinUsdcOut] = useState('0')
  const [status, setStatus] = useState<string>('')

  const dolomiteAddress = import.meta.env.VITE_DOLOMITE_MARGIN as string | undefined
  const guardianAddress = import.meta.env.VITE_GUARDIAN_CONTRACT as string | undefined

  const expectedChainId = NETWORKS[selectedNetwork].chainId

  const marginDisplay = useMemo(() => {
    if (!computedMargin) return '--'
    if (computedMargin === 'no borrow') return 'No borrow (safe)'
    return `${computedMargin} bps`
  }, [computedMargin])

  const connectWallet = async () => {
    if (!window.ethereum) {
      setStatus('MetaMask not found. Install the extension to continue.')
      return
    }

    const provider = new BrowserProvider(window.ethereum)
    const accounts = await provider.send('eth_requestAccounts', [])
    console.log('[connectWallet] eth_requestAccounts returned:', accounts)
    const network = await provider.getNetwork()
    const activeChainId = Number(network.chainId)
    setWalletAddress(accounts[0] ?? '')
    setChainId(activeChainId)
    const detectedNetwork = NETWORK_BY_CHAIN_ID[activeChainId]
    if (detectedNetwork) {
      setSelectedNetwork(detectedNetwork)
    }
    setStatus('Wallet connected.')
  }

  useEffect(() => {
    if (!window.ethereum?.request) return

    const handleAccountsChanged = (...args: unknown[]) => {
      const accounts = Array.isArray(args[0]) ? (args[0] as string[]) : []
      console.log('[handleAccountsChanged] accounts:', accounts)
      setWalletAddress(accounts[0] ?? '')
    }

    const handleChainChanged = (...args: unknown[]) => {
      const chainIdHex = typeof args[0] === 'string' ? args[0] : ''
      const nextChainId = Number(chainIdHex)
      setChainId(nextChainId)
      const detectedNetwork = NETWORK_BY_CHAIN_ID[nextChainId]
      if (detectedNetwork) {
        setSelectedNetwork(detectedNetwork)
      }
    }

    window.ethereum.request({ method: 'eth_accounts' }).then((accounts) => {
      console.log('[useEffect] eth_accounts returned:', accounts)
      if (Array.isArray(accounts)) {
        handleAccountsChanged(accounts as string[])
      }
    })

    window.ethereum.on?.('accountsChanged', handleAccountsChanged)
    window.ethereum.on?.('chainChanged', handleChainChanged)

    return () => {
      window.ethereum?.removeListener?.('accountsChanged', handleAccountsChanged)
      window.ethereum?.removeListener?.('chainChanged', handleChainChanged)
    }
  }, [])

  const loadPosition = async () => {
    try {
      if (!window.ethereum) {
        setStatus('MetaMask not found.')
        return
      }
      if (!dolomiteAddress) {
        setStatus('Missing VITE_DOLOMITE_MARGIN in .env')
        return
      }
      if (!accountOwner) {
        setStatus('Enter the account owner address.')
        return
      }
      if (!ethers.isAddress(accountOwner)) {
        setStatus('Account owner must be a valid address.')
        return
      }

      const provider = new BrowserProvider(window.ethereum)
      const network = await provider.getNetwork()
      const activeChainId = Number(network.chainId)
      setChainId(activeChainId)
      const detectedNetwork = NETWORK_BY_CHAIN_ID[activeChainId]
      if (detectedNetwork && detectedNetwork !== selectedNetwork) {
        setSelectedNetwork(detectedNetwork)
      }
      if (activeChainId !== expectedChainId) {
        setStatus(`Wrong network. Switch MetaMask to ${NETWORKS[selectedNetwork].label}.`)
        return
      }

      const dolomite = new Contract(dolomiteAddress, DOL_MARGIN_ABI, provider)
      const [supply, borrow] = await dolomite.getAccountValues(accountOwner, BigInt(accountNumber || '0'))
      const supplyValueBn = supply as bigint
      const borrowValueBn = borrow as bigint
      setSupplyValue(supplyValueBn.toString())
      setBorrowValue(borrowValueBn.toString())

      if (borrowValueBn === 0n) {
        setComputedMargin('no borrow')
      } else {
        const marginBps =
          ((supplyValueBn > borrowValueBn ? supplyValueBn - borrowValueBn : 0n) * 10_000n) /
          borrowValueBn
        setComputedMargin(marginBps.toString())
      }
      setStatus('Position loaded from Dolomite.')

      if (guardianAddress) {
        const guardian = new Contract(guardianAddress, GUARDIAN_ABI, provider)
        const safety = await guardian.safetyMarginBps()
        setSafetyMargin(Number(safety))
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to load position.'
      setStatus(`Read failed: ${message}`)
    }
  }

  const adjustPosition = async () => {
    try {
      if (!window.ethereum) {
        setStatus('MetaMask not found.')
        return
      }
      if (!guardianAddress) {
        setStatus('Missing VITE_GUARDIAN_CONTRACT in .env')
        return
      }
      if (!accountOwner) {
        setStatus('Enter the account owner address.')
        return
      }
      if (!ethers.isAddress(accountOwner)) {
        setStatus('Account owner must be a valid address.')
        return
      }

      const provider = new BrowserProvider(window.ethereum)
      const network = await provider.getNetwork()
      const activeChainId = Number(network.chainId)
      if (activeChainId !== expectedChainId) {
        setStatus(`Wrong network. Switch MetaMask to ${NETWORKS[selectedNetwork].label}.`)
        return
      }

      const signer = await provider.getSigner()
      const guardian = new Contract(guardianAddress, GUARDIAN_ABI, signer)

      const wbtcParsed = ethers.parseUnits(wbtcAmountIn || '0', 8)
      const usdcParsed = ethers.parseUnits(minUsdcOut || '0', 6)

      const tx = await guardian.adjustPosition(
        accountOwner,
        BigInt(accountNumber || '0'),
        wbtcParsed,
        usdcParsed,
      )
      setStatus(`Submitted tx: ${tx.hash}`)
      await tx.wait()
      setStatus('Position adjustment confirmed.')
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to adjust position.'
      setStatus(`Adjustment failed: ${message}`)
    }
  }

  return (
    <div className="app">
      <header className="hero">
        <div>
          <p className="eyebrow">Dolomite Guardian • WBTC/USDC</p>
          <h1>Protect your Dolomite WBTC long before liquidation.</h1>
          <p className="subtitle">
            Monitor your safety margin and trigger automated WBTC→USDC conversion when it drops below your
            threshold.
          </p>
        </div>
        <div className="wallet-card">
          <div>
            <span>Connected wallet</span>
            <strong className="wallet-address">{walletAddress || '—'}</strong>
          </div>
          <button className="primary" onClick={connectWallet}>
            {walletAddress ? 'Reconnect' : 'Connect MetaMask'}
          </button>
          <div className="network-row">
            <label htmlFor="network">Network</label>
            <select
              id="network"
              value={selectedNetwork}
              onChange={(event) => setSelectedNetwork(event.target.value)}
            >
              {Object.entries(NETWORKS).map(([key, network]) => (
                <option key={key} value={key}>
                  {network.label}
                </option>
              ))}
            </select>
          </div>
          <p className={`network-hint ${chainId === expectedChainId ? 'ok' : 'warn'}`}>
            {chainId === expectedChainId
              ? `Wallet network OK (chain ${chainId}).`
              : chainId
                ? `Wallet is on chain ${chainId}. Switch to ${NETWORKS[selectedNetwork].label}.`
                : 'Connect wallet to verify network.'}
          </p>
        </div>
      </header>

      <section className="grid">
        <div className="panel">
          <h2>Position lookup</h2>
          <div className="field">
            <label>Account owner</label>
            <input
              placeholder="0x..."
              value={accountOwner}
              onChange={(event) => setAccountOwner(event.target.value)}
            />
          </div>
          <div className="field">
            <label>Account number</label>
            <input
              value={accountNumber}
              onChange={(event) => setAccountNumber(event.target.value)}
            />
          </div>
          <button className="ghost" onClick={loadPosition}>
            Read from Dolomite
          </button>
          <div className="metrics">
            <div>
              <span>Supply value</span>
              <strong>{supplyValue || '--'}</strong>
            </div>
            <div>
              <span>Borrow value</span>
              <strong>{borrowValue || '--'}</strong>
            </div>
            <div>
              <span>Safety margin</span>
              <strong>{marginDisplay}</strong>
            </div>
            <div>
              <span>Contract threshold</span>
              <strong>{safetyMargin !== null ? `${safetyMargin} bps` : '--'}</strong>
            </div>
          </div>
        </div>

        <div className="panel">
          <h2>Adjustment parameters</h2>
          <p className="panel-note">
            This prototype calls the Guardian contract and delegates swaps to a rebalancer. Amounts use WBTC (8
            decimals) and USDC (6 decimals).
          </p>
          <div className="field">
            <label>WBTC to swap</label>
            <input value={wbtcAmountIn} onChange={(event) => setWbtcAmountIn(event.target.value)} />
          </div>
          <div className="field">
            <label>Minimum USDC out</label>
            <input value={minUsdcOut} onChange={(event) => setMinUsdcOut(event.target.value)} />
          </div>
          <button className="primary" onClick={adjustPosition}>
            Execute adjustment
          </button>
          <div className="status">
            <span>Status</span>
            <p>{status || 'Ready.'}</p>
          </div>
        </div>
      </section>
    </div>
  )
}

export default App
