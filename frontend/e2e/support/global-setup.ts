/**
 * Before the suite: the stack must answer, and the dev realm must have `expert2` (an EXPERT
 * without OTP, for the MFA enforcement scenario). Also measures whether the configured
 * embedding model tells unrelated texts apart (E2E_EMBEDDINGS=ok|degenerate): scenarios that
 * depend on retrieval quality skip, with that reason, when it doesn't. The realm file defines it, but Keycloak
 * imports a realm only once, so an existing stack gets it through the admin API (allowed
 * from localhost only, KC_ADMIN_ALLOW).
 */

import { execFileSync } from "node:child_process";

import { request } from "@playwright/test";

import { COMPOSE, REPO, secret, SITE } from "./stack";

const EMBED_CHECK = `
import asyncio, math
from spoldzielnia_ai.core.settings import Settings
from spoldzielnia_ai.llm.gateway import LiteLLMGateway
a, b = asyncio.run(LiteLLMGateway(Settings().llm).embed(
    ["Jak leczyć warrozę kwasem szczawiowym zimą?", "SELECT count(*) FROM invoices WHERE paid"]))
print(sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b)))
`;

function embeddingsSeparate(): boolean {
  try {
    const out = execFileSync("docker", [...COMPOSE, "exec", "-T", "api", "python", "-c", EMBED_CHECK], {
      cwd: REPO,
      timeout: 120_000,
    });
    const cosine = Number(out.toString().trim().split("\n").pop());
    return Number.isFinite(cosine) && cosine < 0.8;
  } catch {
    return false;
  }
}

const AUTH = SITE.replace("://", "://auth.");

export default async function globalSetup(): Promise<void> {
  const http = await request.newContext({ ignoreHTTPSErrors: true });
  const health = await http.get(`${SITE}/api/healthz`).catch(() => null);
  if (!health?.ok()) throw new Error(`${SITE} is not up: run \`make up\` first`);
  // Inherited by the test workers.
  process.env.E2E_EMBEDDINGS = embeddingsSeparate() ? "ok" : "degenerate";

  const token = await http.post(`${AUTH}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: "password", client_id: "admin-cli", username: "admin", password: secret("keycloak_admin_password") },
  });
  if (!token.ok()) throw new Error(`Keycloak admin login failed: ${token.status()}`);
  const headers = { Authorization: `Bearer ${(await token.json()).access_token}` };
  const users = `${AUTH}/admin/realms/spoldzielnia/users`;
  const existing = await (await http.get(`${users}?username=expert2&exact=true`, { headers })).json();
  if (existing.length === 0) {
    const created = await http.post(users, {
      headers,
      data: {
        username: "expert2", email: "expert2@spoldzielnia.localhost", emailVerified: true,
        firstName: "Expert", lastName: "Two", enabled: true,
        credentials: [{ type: "password", value: secret("dev_user_password"), temporary: false }],
      },
    });
    if (!created.ok()) throw new Error(`creating expert2 failed: ${created.status()}`);
    const [user] = await (await http.get(`${users}?username=expert2&exact=true`, { headers })).json();
    const roles = await (await http.get(`${AUTH}/admin/realms/spoldzielnia/roles`, { headers })).json();
    const wanted = roles.filter((r: { name: string }) => r.name === "MEMBER" || r.name === "EXPERT");
    await http.post(`${users}/${user.id}/role-mappings/realm`, { headers, data: wanted });
  }
  await http.dispose();
}
