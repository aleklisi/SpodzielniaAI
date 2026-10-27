import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

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

// The UI language (14.2): next-intl without i18n routing, configured in src/i18n/request.ts.
const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

export default withNextIntl(nextConfig);
