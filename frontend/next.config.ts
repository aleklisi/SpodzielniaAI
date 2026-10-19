import type { NextConfig } from "next";

// Nonce-based CSP (src/proxy.ts) needs every page rendered per request, so Cache Components /
// partial prerendering stay off: a prerendered shell could not carry the request's nonce.
const nextConfig: NextConfig = {
  output: "standalone", // frontend/Dockerfile runs .next/standalone/server.js
  poweredByHeader: false,
  reactStrictMode: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
