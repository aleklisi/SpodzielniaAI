"use client";

import { createContext, useContext, type ReactNode } from "react";

import { DEFAULT_BRANDING, type Branding } from "@/lib/branding-css";

const BrandingContext = createContext<Branding>(DEFAULT_BRANDING);

/** The branding the root layout fetched, for the menu, the top bar and the home page (13.4). */
export function BrandingProvider({ branding, children }: { branding: Branding; children: ReactNode }) {
  return <BrandingContext.Provider value={branding}>{children}</BrandingContext.Provider>;
}

export function useBranding(): Branding {
  return useContext(BrandingContext);
}
