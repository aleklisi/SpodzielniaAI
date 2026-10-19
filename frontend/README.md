# Spółdzielnia AI — frontend

Next.js 16 (App Router, TypeScript strict, Tailwind v4). It runs behind Caddy on the same origin
as the API (`/` → this app, `/api/*` → FastAPI), so the backend's `__Host-` cookies and
`SameSite=Strict` just work and the browser never sees an OIDC token.

```sh
npm ci
npm run lint && npm run typecheck && npm test   # per-chunk checks
npm run gen:api                                 # after backend route/schema changes
```

- `src/proxy.ts`: per-request CSP nonce (Trusted Types required) and security headers.
- `src/instrumentation-client.ts`: the Trusted Types `default` policy (only our own
  `/_next/static/` chunks may be loaded as scripts).
- `src/lib/api.ts`: typed client (`openapi-fetch`); CSRF header, shield/auth errors.
- `openapi.json` and `src/lib/api-schema.d.ts` are generated (`npm run gen:api`); a backend
  test fails when they are out of date.

In the stack: `make up` builds the `frontend` image (`frontend/Dockerfile`, distroless, non-root).
