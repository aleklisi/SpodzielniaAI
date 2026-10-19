import { NextResponse, type NextRequest } from "next/server";

import { contentSecurityPolicy, nonce, STATIC_HEADERS } from "@/lib/security-headers";

export function proxy(request: NextRequest) {
  const n = nonce();
  const csp = contentSecurityPolicy(n, process.env.NODE_ENV === "development");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", n);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  for (const [name, value] of Object.entries(STATIC_HEADERS)) {
    response.headers.set(name, value);
  }
  return response;
}

export const config = {
  matcher: [
    {
      // /api is the FastAPI backend (routed by Caddy, never reaches Next.js).
      source: "/((?!api|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
