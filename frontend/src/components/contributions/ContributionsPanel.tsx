"use client";

import Link from "next/link";
import { useState } from "react";

import { RevokeDialog } from "@/components/contributions/RevokeDialog";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusPill } from "@/components/ui/StatusPill";
import { useToast } from "@/components/ui/Toast";
import { blocksWord, describeResult, useConsent, useHistory, type ConsentAction, type HistoryEntry } from "@/lib/contributions";
import { useContributions, type Contribution } from "@/lib/wiki";

const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "medium", timeStyle: "short" });

function counts(c: Contribution) {
  const by = { ACTIVE: 0, SUSPENDED: 0, REVOKED: 0 };
  for (const b of c.blocks) by[b.consent_status] += 1;
  return by;
}

export function ContributionsPanel() {
  const contributions = useContributions();
  const consent = useConsent();
  const toast = useToast();
  const [revoke, setRevoke] = useState<{ ids: string[] | null; label: string; coAuthored: boolean } | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  async function act(action: Exclude<ConsentAction, "revoke">, ids: string[] | null, key: string) {
    setPending(key);
    try {
      const result = await consent.mutateAsync({ action, articleIds: ids });
      toast(describeResult(action, result), "ok");
    } catch {
      toast("Nie udało się zmienić zgody. Spróbuj ponownie.", "error");
    } finally {
      setPending(null);
    }
  }

  const rows = contributions.data ?? [];
  const live = rows.filter((c) => counts(c).ACTIVE + counts(c).SUSPENDED > 0);
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-display text-2xl font-semibold">Moje kontrybucje</h1>
        <p className="max-w-2xl text-sm text-text-muted">
          Twój wkład w bazę wiedzy pozostaje Twój. Możesz go w każdej chwili wstrzymać (ukryć i wyłączyć z odpowiedzi
          asystenta) albo trwale usunąć.
        </p>
      </div>
      {live.length > 0 && (
        <div className="flex flex-wrap gap-2" aria-label="Wszystkie kontrybucje">
          <Button variant="secondary" busy={pending === "all:suspend"} onClick={() => act("suspend", null, "all:suspend")}>
            Wstrzymaj wszystko
          </Button>
          <Button variant="secondary" busy={pending === "all:resume"} onClick={() => act("resume", null, "all:resume")}>
            Wznów wszystko
          </Button>
          <Button
            variant="danger"
            onClick={() => setRevoke({ ids: null, label: "cały swój wkład we wszystkich artykułach", coAuthored: rows.some((c) => c.co_authored) })}
          >
            Usuń trwale wszystko
          </Button>
        </div>
      )}
      {contributions.isPending && <p className="text-text-muted">Ładowanie…</p>}
      {contributions.data?.length === 0 && <p className="text-text-muted">Nie masz jeszcze kontrybucji w bazie wiedzy.</p>}
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-text-muted">
              <tr>
                <th className="py-2 pr-3 font-medium">Artykuł</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 pr-3 font-medium">Twoje bloki</th>
                <th className="py-2 font-medium">Działania</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const n = counts(c);
                const key = c.article_id;
                return (
                  <tr key={key} className="border-t border-border align-top">
                    <td className="py-3 pr-3">
                      <Link href={`/wiki/${c.article_id}`} className="font-medium hover:text-primary-strong">
                        {c.title}
                      </Link>
                      {c.co_authored && (
                        <Badge tone="accent" className="ml-2">
                          współtworzony
                        </Badge>
                      )}
                    </td>
                    <td className="py-3 pr-3">
                      <StatusPill status={c.status} />
                    </td>
                    <td className="py-3 pr-3">
                      {n.ACTIVE > 0 && <div>{n.ACTIVE} aktywne</div>}
                      {n.SUSPENDED > 0 && <div className="text-warn">{n.SUSPENDED} wstrzymane</div>}
                      {n.REVOKED > 0 && <div className="text-text-muted">{n.REVOKED} usunięte</div>}
                    </td>
                    <td className="py-3">
                      <div className="flex flex-wrap gap-2">
                        {n.ACTIVE > 0 && (
                          <Button variant="secondary" busy={pending === `${key}:suspend`} onClick={() => act("suspend", [key], `${key}:suspend`)}>
                            Wstrzymaj
                          </Button>
                        )}
                        {n.SUSPENDED > 0 && (
                          <Button variant="secondary" busy={pending === `${key}:resume`} onClick={() => act("resume", [key], `${key}:resume`)}>
                            Wznów
                          </Button>
                        )}
                        {n.ACTIVE + n.SUSPENDED > 0 && (
                          <Button
                            variant="ghost"
                            className="text-danger"
                            onClick={() =>
                              setRevoke({
                                ids: [key],
                                label: `${n.ACTIVE + n.SUSPENDED} ${blocksWord(n.ACTIVE + n.SUSPENDED)} w „${c.title}”`,
                                coAuthored: c.co_authored,
                              })
                            }
                          >
                            Usuń trwale
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <History />
      <RevokeDialog
        open={revoke !== null}
        onClose={() => setRevoke(null)}
        articleIds={revoke?.ids ?? null}
        scopeLabel={revoke?.label ?? ""}
        coAuthored={revoke?.coAuthored ?? false}
        onDone={(result) => {
          setRevoke(null);
          toast(describeResult("revoke", result), "ok");
        }}
      />
    </div>
  );
}

const EVENT_LABEL: Record<HistoryEntry["event"], string> = {
  SUSPEND: "Wstrzymanie",
  RESUME: "Wznowienie",
  REVOKE: "Trwałe usunięcie",
  ARTICLE_DEGRADED: "Artykuł wrócił do recenzji",
};

function History() {
  const history = useHistory();
  if (!history.data?.length) return null;
  return (
    <Card>
      <h2 className="mb-3 font-display text-lg font-semibold">Historia zmian zgód</h2>
      <ol className="flex flex-col gap-2 text-sm">
        {history.data.map((e, i) => (
          <li key={i} className="flex flex-wrap justify-between gap-2 border-b border-border pb-2 last:border-0">
            <span>
              {EVENT_LABEL[e.event]}
              {e.blocks != null && e.event !== "ARTICLE_DEGRADED" && ` · ${e.blocks} ${blocksWord(e.blocks)}`}
              {e.scope === "all" && " · wszystkie artykuły"}
              {e.article_id && (
                <>
                  {" · "}
                  <Link href={`/wiki/${e.article_id}`} className="underline">
                    artykuł
                  </Link>
                </>
              )}
            </span>
            <time dateTime={e.occurred_at} className="text-text-muted">
              {DATE.format(new Date(e.occurred_at))}
            </time>
          </li>
        ))}
      </ol>
    </Card>
  );
}
