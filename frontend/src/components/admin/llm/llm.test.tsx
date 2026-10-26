import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { previewKind, ProbeReport } from "@/components/admin/llm/Catalog";
import { LlmConsole } from "@/components/admin/llm/LlmConsole";
import { changes } from "@/components/admin/llm/Routing";
import { eligibleModels, llmError, type LlmModel, type LlmStatus, type Provider, type Revision } from "@/lib/llm-admin";
import { ApiError } from "@/lib/api";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const NOW = new Date().toISOString();
const LOCAL = "01900000-0000-7000-8000-000000000001";
const CLOUD = "01900000-0000-7000-8000-000000000002";
const BIELIK = "01900000-0000-7000-8000-0000000000a1";
const QWEN = "01900000-0000-7000-8000-0000000000a2";
const GPT = "01900000-0000-7000-8000-0000000000a3";
const EMB = "01900000-0000-7000-8000-0000000000a4";

function status(overrides: Partial<LlmStatus> = {}): LlmStatus {
  return {
    config_source: "db",
    allow_cloud: true,
    local_endpoints: ["http://llm:11434", "http://vllm:8000"],
    cloud_hosts: ["api.example.com"],
    private_data_endpoints: ["http://llm:11434", "http://vllm:8000"],
    missing_purposes: [],
    egress_proxy: true,
    probe_max_age_hours: 24,
    active_revision: 1,
    process_revision: 1,
    purposes: [
      { purpose: "PII_SCRUB", capability: "CHAT", private_data: true },
      { purpose: "PRIVATE_NOTE", capability: "CHAT", private_data: true },
      { purpose: "PROMOTION", capability: "CHAT", private_data: true },
      { purpose: "VLM", capability: "VLM", private_data: true },
      { purpose: "EMBED", capability: "EMBED", private_data: true },
      { purpose: "RAG", capability: "CHAT", private_data: false },
    ],
    ...overrides,
  };
}

function provider(id: string, name: string, kind: "LOCAL" | "CLOUD", overrides: Partial<Provider> = {}): Provider {
  return {
    id, name, kind, allowed: true, base_url: kind === "LOCAL" ? "http://llm:11434/v1" : "https://api.example.com/v1",
    litellm_provider: "openai", key_set: kind === "CLOUD", key_hint: kind === "CLOUD" ? "WXYZ" : null,
    inherit_env_key: kind === "LOCAL", structured_output: null, timeout_seconds: null, disabled: false,
    in_active: kind === "LOCAL", private_data_cleared: kind === "LOCAL", clearable: kind === "LOCAL",
    cleared_region: kind === "LOCAL" ? "serwer spółdzielni" : null, cleared_retention: kind === "LOCAL" ? "brak" : null,
    cleared_contract_ref: kind === "LOCAL" ? ".env" : null, cleared_at: kind === "LOCAL" ? NOW : null,
    removal_blocked: kind === "LOCAL" ? "provider_in_use" : null, removal_archives: false, removal_draft: null,
    created_at: NOW, updated_at: NOW, ...overrides,
  };
}

function model(id: string, providerId: string, name: string, capability: "CHAT" | "VLM" | "EMBED" = "CHAT", probed = true): LlmModel {
  return {
    id, provider_id: providerId, model_name: name, capability, temperature: null, max_tokens: null,
    embed_dimension: capability === "EMBED" ? 1024 : null, notes: null, disabled: false, in_active: false,
    last_probe_at: probed ? NOW : null, last_probe_ok: probed ? true : null, last_probe_report: null,
  };
}

const PROVIDERS = [provider(LOCAL, "local", "LOCAL"), provider(CLOUD, "cloud", "CLOUD")];
const MODELS = [
  model(BIELIK, LOCAL, "bielik-11b"),
  model(QWEN, LOCAL, "qwen3-32b", "CHAT", false),
  model(GPT, CLOUD, "gpt-x"),
  model(EMB, LOCAL, "gemma-embed", "EMBED"),
];
const ROUTING = { PII_SCRUB: BIELIK, PRIVATE_NOTE: BIELIK, PROMOTION: BIELIK, RAG: BIELIK, EMBED: EMB };

function revision(number: number, state: Revision["status"], routing: Record<string, string>): Revision {
  return {
    number, status: state, comment: null, based_on: number > 1 ? 1 : null, rolled_back_from: null, created_by: null,
    created_at: NOW, activated_by: null, activated_at: state === "DRAFT" ? null : NOW, routing,
    routes:
      state === "DRAFT"
        ? null
        : Object.fromEntries(
            Object.entries(routing).map(([p, id]) => [p, { model_id: id, name: `local/${MODELS.find((m) => m.id === id)?.model_name}`, kind: "LOCAL" as const, private_data: true }]),
          ),
  };
}

function mount(revisions: Revision[], s: LlmStatus = status(), providers: Provider[] = PROVIDERS) {
  server.use(
    http.get(`${API}/admin/llm/status`, () => HttpResponse.json(s)),
    http.get(`${API}/admin/llm/providers`, () => HttpResponse.json(providers)),
    http.get(`${API}/admin/llm/models`, () => HttpResponse.json(MODELS)),
    http.get(`${API}/admin/llm/revisions`, () => HttpResponse.json(revisions)),
    http.get(`${API}/admin/llm/playground/presets`, () =>
      HttpResponse.json([
        { key: "pl_qa", title: "Pytanie merytoryczne", prompt: "Wyjaśnij…", private_data: false, structured: false },
        { key: "summary_pii", title: "Streszczenie z danymi", prompt: "Streść…", private_data: true, structured: false },
      ]),
    ),
  );
  return renderWithClient(<LlmConsole />);
}

describe("helpers", () => {
  it("previews the kind the backend will derive", () => {
    const s = status();
    expect(previewKind("http://llm:11434/v1", s)).toBe("LOCAL");
    expect(previewKind("http://vllm:8000/v1/", s)).toBe("LOCAL");
    expect(previewKind("https://api.example.com/v1", s)).toBe("CLOUD");
    expect(previewKind("http://api.example.com/v1", s)).toBeNull();
    expect(previewKind("http://postgres:5432", s)).toBeNull();
    expect(previewKind("https://user:pw@api.example.com/v1", s)).toBeNull();
    expect(previewKind("nonsense", s)).toBeNull();
  });

  it("offers private purposes the models of cleared providers only", () => {
    const priv = eligibleModels({ capability: "CHAT", private_data: true }, MODELS, PROVIDERS).map((m) => m.model_name);
    const any = eligibleModels({ capability: "CHAT", private_data: false }, MODELS, PROVIDERS).map((m) => m.model_name);
    expect(priv).toEqual(["bielik-11b", "qwen3-32b"]);
    expect(any).toEqual(["bielik-11b", "qwen3-32b", "gpt-x"]);
    const clearedCloud = [
      provider(LOCAL, "local", "LOCAL", { private_data_cleared: false }),
      provider(CLOUD, "cloud", "CLOUD", { private_data_cleared: true }),
    ];
    const swapped = eligibleModels({ capability: "CHAT", private_data: true }, MODELS, clearedCloud).map((m) => m.model_name);
    expect(swapped).toEqual(["gpt-x"]);
  });

  it("lists changes and marks cloud-bound purposes", () => {
    const active = revision(1, "ACTIVE", ROUTING);
    const draft = revision(2, "DRAFT", { ...ROUTING, RAG: GPT, PROMOTION: QWEN });
    expect(changes(draft, active, MODELS, PROVIDERS)).toEqual([
      { purpose: "RAG", before: "local/bielik-11b", after: "cloud/gpt-x", cloud: true },
      { purpose: "PROMOTION", before: "local/bielik-11b", after: "local/qwen3-32b", cloud: false },
    ]);
  });

  it("explains backend error codes", () => {
    const error = new ApiError(422, { detail: { error: "provider_not_cleared", purpose: "PROMOTION" } });
    expect(llmError(error)).toMatch(/dopuszczonego do danych prywatnych/);
    expect(llmError(new ApiError(429, { detail: { error: "rate_limited" } }))).toMatch(/Limit/);
  });

  it("renders a probe report without model output", () => {
    renderWithClient(
      <ProbeReport
        report={{
          ok: true,
          recommended_structured_output: "prompt",
          checks: [
            { name: "reply", ok: true, latency_ms: 120, tokens: 8 },
            { name: "json_json_schema", ok: false, latency_ms: 30, error: "LocalModelRejected" },
            { name: "json_prompt", ok: true, latency_ms: 200 },
          ],
        }}
      />,
    );
    expect(screen.getByText(/JSON \(response_format\)/)).toHaveTextContent("✗");
    expect(screen.getByText("schemat w poleceniu")).toBeInTheDocument();
  });

  it("offers speech-to-text only transcription models of cleared providers", () => {
    const whisper = { ...model("01900000-0000-7000-8000-0000000000a5", LOCAL, "whisper-large-v3"), capability: "TRANSCRIBE" as const };
    const cloudWhisper = { ...model("01900000-0000-7000-8000-0000000000a6", CLOUD, "voxtral-mini"), capability: "TRANSCRIBE" as const };
    const all = [...MODELS, whisper, cloudWhisper];
    const names = eligibleModels({ capability: "TRANSCRIBE", private_data: true }, all, PROVIDERS).map((m) => m.model_name);
    expect(names).toEqual(["whisper-large-v3"]);
  });

  it("shows the word error rate of a transcription probe", () => {
    renderWithClient(<ProbeReport report={{ ok: true, checks: [{ name: "transcribe", ok: true, latency_ms: 900, word_error_rate: 0.167 }] }} />);
    expect(screen.getByText(/Transkrypcja nagrania testowego/)).toHaveTextContent("błędne słowa 17%");
  });
});

describe("routing", () => {
  it("locks private purposes to cleared providers and confirms activation with a cloud warning", async () => {
    const draft = revision(2, "DRAFT", { ...ROUTING });
    let routed: unknown = null;
    let activated = false;
    server.use(
      http.put(`${API}/admin/llm/revisions/2/routing`, async ({ request }) => {
        routed = await request.json();
        return HttpResponse.json({ ...draft, routing: { ...ROUTING, RAG: GPT } });
      }),
      http.post(`${API}/admin/llm/revisions/2/activate`, () => {
        activated = true;
        return HttpResponse.json(revision(2, "ACTIVE", { ...ROUTING, RAG: GPT }));
      }),
    );
    mount([revision(2, "DRAFT", { ...ROUTING, RAG: GPT }), revision(1, "ACTIVE", ROUTING)]);

    const promotion = await screen.findByLabelText("Model dla: Wnioski do Wiki");
    const promotionOptions = within(promotion).getAllByRole("option").map((o) => o.textContent);
    expect(promotionOptions).toEqual(["— brak —", "local/bielik-11b", "local/qwen3-32b"]);
    const rag = screen.getByLabelText("Model dla: Czat spółdzielni (RAG)");
    expect(within(rag).getAllByRole("option").map((o) => o.textContent)).toContain("cloud/gpt-x (chmura)");
    expect(screen.getAllByText("🔒 dane prywatne")).toHaveLength(5);
    // 10.3: switching the embedding model is routine (a new index version is built in the background).
    expect(screen.getByLabelText("Model dla: Embeddingi")).toBeEnabled();
    expect(screen.getByText(/indeks przebuduje się w tle/)).toBeInTheDocument();

    await userEvent.selectOptions(promotion, QWEN);
    await waitFor(() => expect(routed).toEqual({ routes: { PROMOTION: QWEN } }));

    await userEvent.click(screen.getByRole("button", { name: "Aktywuj rewizję 2" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("list", { name: "Zmiany" })).toHaveTextContent("Czat spółdzielni (RAG): local/bielik-11b → cloud/gpt-x");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Czat spółdzielni (RAG)");
    await userEvent.click(within(dialog).getByRole("button", { name: "Aktywuj" }));
    await waitFor(() => expect(activated).toBe(true));
  });

  it("starts a draft from the active revision", async () => {
    let created = false;
    server.use(
      http.post(`${API}/admin/llm/revisions`, () => {
        created = true;
        return HttpResponse.json(revision(2, "DRAFT", ROUTING), { status: 201 });
      }),
    );
    mount([revision(1, "ACTIVE", ROUTING)]);
    await userEvent.click(await screen.findByRole("button", { name: "Nowy szkic zmian" }));
    await waitFor(() => expect(created).toBe(true));
  });
});

describe("providers", () => {
  it("never pre-fills keys and blocks cloud endpoints when the server forbids them", async () => {
    mount([revision(1, "ACTIVE", ROUTING)], status({ allow_cloud: false }));
    await userEvent.click(await screen.findByRole("tab", { name: "Dostawcy" }));
    expect(screen.getByText("••••WXYZ")).toBeInTheDocument();
    const cloudRow = screen.getByRole("row", { name: /cloud/ });
    expect(within(cloudRow).getByRole("button", { name: "Testuj połączenie" })).toBeDisabled();

    await userEvent.click(within(cloudRow).getByRole("button", { name: "Edytuj" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText(/Klucz API/)).toHaveValue("");
    expect(within(dialog).getByText(/chmura jest wyłączona/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Zapisz" })).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Anuluj" }));

    await userEvent.click(screen.getByRole("button", { name: "Dodaj dostawcę" }));
    const form = await screen.findByRole("dialog");
    await userEvent.type(within(form).getByLabelText(/Adres API/), "http://10.0.0.5:8000/v1");
    expect(within(form).getByText("Adres spoza listy dozwolonych serwerów.")).toBeInTheDocument();
  });

  it("shows the models a connection test found", async () => {
    server.use(
      http.post(`${API}/admin/llm/providers/${LOCAL}/test`, () =>
        HttpResponse.json({ ok: true, status: 200, latency_ms: 12, models: ["bielik-11b", "qwen3-32b"] }),
      ),
    );
    mount([revision(1, "ACTIVE", ROUTING)]);
    await userEvent.click(await screen.findByRole("tab", { name: "Dostawcy" }));
    const row = screen.getByRole("row", { name: /local/ });
    await userEvent.click(within(row).getByRole("button", { name: "Testuj połączenie" }));
    expect(await within(row).findByRole("status")).toHaveTextContent("Połączono (12 ms) · modele: bielik-11b, qwen3-32b");
  });
});

describe("removal (11.1)", () => {
  it("removes an unused provider and shows the refreshed list", async () => {
    let providers = [...PROVIDERS];
    let removed: string | null = null;
    server.use(
      http.delete(`${API}/admin/llm/providers/${CLOUD}`, () => {
        removed = CLOUD;
        providers = providers.filter((p) => p.id !== CLOUD);
        return new HttpResponse(null, { status: 204 });
      }),
    );
    mount([revision(1, "ACTIVE", ROUTING)], status(), providers);
    server.use(http.get(`${API}/admin/llm/providers`, () => HttpResponse.json(providers))); // reflects the removal
    await userEvent.click(await screen.findByRole("tab", { name: "Dostawcy" }));
    const row = screen.getByRole("row", { name: /cloud/ });
    await userEvent.click(within(row).getByRole("button", { name: "Usuń dostawcę cloud" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/razem z modelami \(1\)/)).toBeInTheDocument();
    expect(within(dialog).getByText(/zostanie usunięty całkowicie/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Usuń" }));
    await waitFor(() => expect(removed).toBe(CLOUD));
    await waitFor(() => expect(screen.queryByRole("row", { name: /cloud/ })).toBeNull());
    expect(screen.getByRole("row", { name: /local/ })).toBeInTheDocument();
    expect(await screen.findByText("Dostawca „cloud” został usunięty.")).toBeInTheDocument();
  });

  it("explains why a provider in use cannot be removed", async () => {
    const providers = [
      provider(LOCAL, "local", "LOCAL"),
      provider(CLOUD, "cloud", "CLOUD", { removal_blocked: "provider_in_draft", removal_draft: 4 }),
    ];
    mount([revision(1, "ACTIVE", ROUTING)], status(), providers);
    await userEvent.click(await screen.findByRole("tab", { name: "Dostawcy" }));
    const local = screen.getByRole("row", { name: /local/ });
    expect(within(local).getByRole("button", { name: "Usuń dostawcę local" })).toBeDisabled();
    expect(within(local).getByText("Używany przez aktywną konfigurację")).toBeInTheDocument();
    const cloud = screen.getByRole("row", { name: /cloud/ });
    expect(within(cloud).getByText("Używany w szkicu rewizji 4")).toBeInTheDocument();
  });

  it("says a provider from past revisions is archived, and shows a refusal", async () => {
    server.use(
      http.delete(`${API}/admin/llm/providers/${CLOUD}`, () =>
        HttpResponse.json({ detail: { error: "provider_in_index" } }, { status: 409 }),
      ),
    );
    const providers = [provider(LOCAL, "local", "LOCAL"), provider(CLOUD, "cloud", "CLOUD", { removal_archives: true })];
    mount([revision(1, "ACTIVE", ROUTING)], status(), providers);
    await userEvent.click(await screen.findByRole("tab", { name: "Dostawcy" }));
    await userEvent.click(within(screen.getByRole("row", { name: /cloud/ })).getByRole("button", { name: "Usuń dostawcę cloud" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/zostanie zarchiwizowany/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Usuń" }));
    expect(await screen.findByText(/obsługuje indeks wyszukiwania/)).toBeInTheDocument();
  });
});

describe("missing models", () => {
  it("lists purposes without a model", async () => {
    mount([revision(1, "ACTIVE", ROUTING)], status({ missing_purposes: ["VLM", "PROMOTION"] }));
    const region = await screen.findByRole("region", { name: "Brakujące modele" });
    expect(within(region).getByText("Opisy zdjęć")).toBeInTheDocument();
    expect(within(region).getByText("Wnioski do Wiki")).toBeInTheDocument();
  });

  it("shows nothing when every purpose has a model", async () => {
    mount([revision(1, "ACTIVE", ROUTING)]);
    await screen.findByRole("heading", { name: "Modele AI" });
    expect(screen.queryByRole("region", { name: "Brakujące modele" })).toBeNull();
  });
});

describe("clearance", () => {
  it("clears a provider with region, retention and contract", async () => {
    let sent: unknown = null;
    server.use(
      http.post(`${API}/admin/llm/providers/${CLOUD}/clearance`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(provider(CLOUD, "cloud", "CLOUD", { private_data_cleared: true, clearable: true }));
      }),
    );
    const providers = [provider(LOCAL, "local", "LOCAL"), provider(CLOUD, "cloud", "CLOUD", { clearable: true })];
    mount([revision(1, "ACTIVE", ROUTING)], status({ private_data_endpoints: ["https://api.example.com"] }), providers);
    await userEvent.click(await screen.findByRole("tab", { name: "Dostawcy" }));
    expect(screen.getByText("https://api.example.com")).toBeInTheDocument();
    const localRow = screen.getByRole("row", { name: /local/ });
    expect(within(localRow).getByText("Dopuszczony do danych prywatnych")).toBeInTheDocument();
    const cloudRow = screen.getByRole("row", { name: /cloud/ });
    await userEvent.click(within(cloudRow).getByRole("button", { name: "Dopuść do danych prywatnych" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/serwer zewnętrzny/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Region przetwarzania"), "UE (Francja)");
    await userEvent.type(within(dialog).getByLabelText("Przechowywanie danych przez dostawcę"), "brak");
    await userEvent.type(within(dialog).getByLabelText(/Umowa/), "DPA 2026/01");
    await userEvent.click(within(dialog).getByRole("button", { name: "Dopuść" }));
    await waitFor(() => expect(sent).toEqual({ region: "UE (Francja)", retention: "brak", contract_ref: "DPA 2026/01" }));
  });

  it("asks for a fresh login when the session is too old", async () => {
    server.use(
      http.post(`${API}/admin/llm/providers/${CLOUD}/clearance`, () =>
        HttpResponse.json({ detail: { error: "reauthentication_required", step_up: "/api/auth/login?prompt=login" } }, { status: 403 }),
      ),
    );
    const providers = [provider(LOCAL, "local", "LOCAL"), provider(CLOUD, "cloud", "CLOUD", { clearable: true })];
    mount([revision(1, "ACTIVE", ROUTING)], status(), providers);
    await userEvent.click(await screen.findByRole("tab", { name: "Dostawcy" }));
    await userEvent.click(within(screen.getByRole("row", { name: /cloud/ })).getByRole("button", { name: "Dopuść do danych prywatnych" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Region przetwarzania"), "UE");
    await userEvent.type(within(dialog).getByLabelText("Przechowywanie danych przez dostawcę"), "brak");
    await userEvent.type(within(dialog).getByLabelText(/Umowa/), "DPA-1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Dopuść" }));
    expect(await within(dialog).findByText(/wymaga niedawnego logowania/)).toBeInTheDocument();
  });

  it("warns that un-clearing an active provider stops its private features", async () => {
    let called = false;
    server.use(
      http.delete(`${API}/admin/llm/providers/${LOCAL}/clearance`, () => {
        called = true;
        return HttpResponse.json(provider(LOCAL, "local", "LOCAL", { private_data_cleared: false }));
      }),
    );
    mount([revision(1, "ACTIVE", ROUTING)]);
    await userEvent.click(await screen.findByRole("tab", { name: "Dostawcy" }));
    await userEvent.click(within(screen.getByRole("row", { name: /local/ })).getByRole("button", { name: "Cofnij dopuszczenie" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/nie przełączy ich na innego dostawcę/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cofnij dopuszczenie" }));
    await waitFor(() => expect(called).toBe(true));
  });
});

describe("playground", () => {
  it("compares models side by side and shows answers as plain text", async () => {
    let sent: unknown = null;
    server.use(
      http.post(`${API}/admin/llm/playground`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({
          results: [
            { model_id: BIELIK, name: "local/bielik-11b", local: true, ok: true, content: "<img src=x onerror=alert(1)>Warroza…", latency_ms: 1500, tokens: 80, json_valid: null, truncated: false },
            { model_id: QWEN, name: "local/qwen3-32b", local: true, ok: false, error: "LocalModelUnavailable", latency_ms: 10 },
          ],
        });
      }),
    );
    const { container } = mount([revision(1, "ACTIVE", ROUTING)]);
    await userEvent.click(await screen.findByRole("tab", { name: "Playground" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /local\/bielik-11b/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /local\/qwen3-32b/ }));
    await userEvent.click(screen.getByRole("button", { name: "Uruchom" }));

    const results = await screen.findByLabelText("Wyniki");
    expect(sent).toEqual({ model_ids: [BIELIK, QWEN], prompt: null, preset: "pl_qa" });
    expect(within(results).getByText(/<img src=x onerror=alert\(1\)>Warroza/)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(within(results).getByText("Błąd: LocalModelUnavailable")).toBeInTheDocument();
    expect(within(results).getByText(/1\.5 s · 80 tokenów/)).toBeInTheDocument();
  });

  it("keeps models of uncleared providers out of private presets", async () => {
    mount([revision(1, "ACTIVE", ROUTING)]);
    await userEvent.click(await screen.findByRole("tab", { name: "Playground" }));
    const gpt = screen.getByRole("checkbox", { name: /cloud\/gpt-x/ });
    await userEvent.click(gpt);
    expect(gpt).toBeChecked();
    await userEvent.selectOptions(screen.getByLabelText("Scenariusz"), "summary_pii");
    expect(gpt).not.toBeChecked();
    expect(gpt).toBeDisabled();
  });
});

describe("history", () => {
  it("restores an archived revision", async () => {
    let restored: unknown = null;
    server.use(
      http.post(`${API}/admin/llm/revisions/1/rollback`, async ({ request }) => {
        restored = await request.json();
        return HttpResponse.json(revision(3, "ACTIVE", ROUTING));
      }),
    );
    mount([revision(2, "ACTIVE", { ...ROUTING, RAG: QWEN }), revision(1, "ARCHIVED", ROUTING)]);
    await userEvent.click(await screen.findByRole("tab", { name: "Historia" }));
    const list = screen.getByRole("list", { name: "Rewizje" });
    expect(within(list).getAllByText(/^Rewizja \d$/).map((e) => e.textContent)).toEqual(["Rewizja 2", "Rewizja 1"]);
    await userEvent.click(within(list).getByRole("button", { name: "Przywróć" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Komentarz/), "powrót");
    await userEvent.click(within(dialog).getByRole("button", { name: "Przywróć" }));
    await waitFor(() => expect(restored).toEqual({ comment: "powrót" }));
  });
});
