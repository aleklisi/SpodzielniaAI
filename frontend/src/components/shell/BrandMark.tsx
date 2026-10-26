"use client";

import { Hexagon } from "lucide-react";

import { cn } from "@/components/ui/cn";
import { useBranding } from "@/lib/branding-context";

/**
 * The organisation's mark (13.4): its logo, the square favicon in the icon rail, or the hexagon
 * without branding. Decorative: the name next to it names the link.
 */
export function BrandMark({ rail = false, className }: { rail?: boolean; className?: string }) {
  const { logo_url, favicon_urls } = useBranding();
  if (!logo_url || !favicon_urls) return <Hexagon aria-hidden className={cn("size-7 shrink-0 fill-primary/25 text-primary", className)} />;
  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin, immutable PNG */}
      <img src={favicon_urls["180"]} alt="" className={cn("size-7 shrink-0 rounded object-contain", rail ? "sb-wide:hidden" : "hidden")} />
      {/* eslint-disable-next-line @next/next/no-img-element -- a same-origin, immutable PNG */}
      <img src={logo_url} alt="" className={cn("h-8 w-auto max-w-32 shrink-0 object-contain", rail && "hidden sb-wide:block", className)} />
    </>
  );
}
