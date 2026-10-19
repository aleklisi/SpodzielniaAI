import { notFound } from "next/navigation";

// Unmatched URLs render app/not-found.tsx inside the root layout (per request, with the CSP
// nonce) instead of Next's built-in 404, whose inline style attributes the CSP blocks.
export default function Missing() {
  notFound();
}
