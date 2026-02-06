/// <reference types="vite/client" />

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] | Record<string, unknown> }) => Promise<unknown>;
      isMetaMask?: boolean;
      on?: (event: 'accountsChanged' | 'chainChanged', handler: (...args: unknown[]) => void) => void;
      removeListener?: (event: 'accountsChanged' | 'chainChanged', handler: (...args: unknown[]) => void) => void;
    };
  }
}

export {};
