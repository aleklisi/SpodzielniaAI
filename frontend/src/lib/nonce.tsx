"use client";

import { createContext, useContext, type ReactNode } from "react";

/** The request's CSP nonce (src/proxy.ts), for client code that must inject a `<style>`
 * itself (the editor). Next.js applies it to its own scripts and styles already. */
const NonceContext = createContext<string | undefined>(undefined);

export function NonceProvider({ nonce, children }: { nonce: string | undefined; children: ReactNode }) {
  return <NonceContext.Provider value={nonce}>{children}</NonceContext.Provider>;
}

export function useNonce(): string | undefined {
  return useContext(NonceContext);
}
