"use client";

import Link from "next/link";
import { useRef, useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { SafeMarkdown } from "@/components/markdown/SafeMarkdown";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { ModelMissing } from "@/components/ui/ModelMissing";
import { loginUrl, NotAuthenticated } from "@/lib/api";
import { isModelMissing, MODEL_MISSING, useFeature } from "@/lib/capabilities";
import { askCooperative, type Ask, type Citation, type RagAnswer } from "@/lib/chat";
import { solveInBackground } from "@/lib/pow";

type Turn =
  | { id: number; question: string; state: "asking" }
  | { id: number; question: string; state: "solving"; tried: number; difficulty: number }
  | { id: number; question: string; state: "answered"; answer: RagAnswer }
  | { id: number; question: string; state: "mfa"; stepUp: string }
  | { id: number; question: string; state: "error"; message: string };

const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "medium" });
const MAX_CHARS = 1000;

export type Solver = typeof solveInBackground;

/**
 * Questions to the cooperative's canonical knowledge. The conversation lives only in this
 * page's memory. Answers arrive as syntheses with article-level citations, rendered inert
 * (SafeMarkdown). The shield's step-up challenges are handled here: proof-of-work is solved
 * in a Web Worker, MFA sends the member to log in again, a lock ends the conversation.
 */
export function ChatView({ ask = askCooperative, solve = solveInBackground }: { ask?: Ask; solve?: Solver }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [locked, setLocked] = useState(false);
  const available = useFeature("wiki_answers");
  const nextId = useRef(0);
  const busy = turns.some((t) => t.state === "asking" || t.state === "solving");

  const update = (id: number, turn: Turn) => setTurns((current) => current.map((t) => (t.id === id ? turn : t)));

  async function run(id: number, text: string) {
    try {
      let outcome = await ask(text);
      if (outcome.kind === "pow") {
        const { nonce, difficulty } = outcome.puzzle;
        update(id, { id, question: text, state: "solving", tried: 0, difficulty });
        const counter = await solve(nonce, difficulty, (tried) =>
          update(id, { id, question: text, state: "solving", tried, difficulty }),
        );
        outcome = await ask(text, `${nonce}:${counter}`);
      }
      switch (outcome.kind) {
        case "answer":
          update(id, { id, question: text, state: "answered", answer: outcome.answer });
          return;
        case "mfa":
          update(id, { id, question: text, state: "mfa", stepUp: outcome.stepUp });
          return;
        case "locked":
          setLocked(true);
          return;
        case "throttled":
          update(id, {
            id,
            question: text,
            state: "error",
            message: `Zadałeś dziś bardzo wiele pytań. Spróbuj ponownie za ${Math.ceil(outcome.retryAfterSeconds / 60)} min.`,
          });
          return;
        case "pow":
          update(id, { id, question: text, state: "error", message: "Weryfikacja nie powiodła się. Spróbuj ponownie." });
          return;
      }
    } catch (error) {
      if (error instanceof NotAuthenticated) {
        window.location.assign(loginUrl("/chat"));
        return;
      }
      const message = isModelMissing(error)
        ? `${MODEL_MISSING}: odpowiedzi spółdzielni są wyłączone.`
        : "Nie udało się uzyskać odpowiedzi. Spróbuj ponownie za chwilę.";
      update(id, { id, question: text, state: "error", message });
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (text.length < 3 || busy) return;
    const id = nextId.current++;
    setTurns((current) => [...current, { id, question: text, state: "asking" }]);
    setQuestion("");
    void run(id, text);
  }

  if (locked) return <LockedNotice />;
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="font-display text-2xl font-semibold">Zapytaj spółdzielnię</h1>
        <p className="text-sm text-text-muted">
          Odpowiedzi powstają wyłącznie na podstawie artykułów zatwierdzonych przez Radę Ekspertów, zawsze ze źródłami.
        </p>
      </div>
      {!available && <ModelMissing what="Zadawanie pytań spółdzielni" />}
      <ol className="flex flex-col gap-6" aria-label="Rozmowa">
        {turns.map((turn) => (
          <li key={turn.id} className="flex flex-col gap-3">
            <p className="self-end rounded-2xl bg-primary/15 px-4 py-2 text-sm">{turn.question}</p>
            <TurnBody turn={turn} />
          </li>
        ))}
      </ol>
      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor="chat-question" className="sr-only">
          Twoje pytanie
        </label>
        <textarea
          id="chat-question"
          rows={3}
          maxLength={MAX_CHARS}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="Np. jak leczyć warrozę zimą?"
          className="rounded-xl border border-border bg-surface px-3 py-2"
        />
        <div className="flex items-center justify-between text-xs text-text-muted">
          <span>
            {question.length}/{MAX_CHARS}
          </span>
          <Button type="submit" busy={busy} disabled={!available || question.trim().length < 3}>
            Zapytaj
          </Button>
        </div>
      </form>
    </div>
  );
}

function TurnBody({ turn }: { turn: Turn }) {
  switch (turn.state) {
    case "asking":
      return <p className="text-sm text-text-muted" role="status">Szukam w bazie wiedzy…</p>;
    case "solving":
      return (
        <Card role="status" className="text-sm">
          <p>Weryfikacja bezpieczeństwa w toku… To potrwa chwilę.</p>
          <p className="mt-1 text-xs text-text-muted">
            Sprawdzono {turn.tried.toLocaleString("pl-PL")} z ok. {(2 ** turn.difficulty).toLocaleString("pl-PL")} kombinacji
          </p>
        </Card>
      );
    case "mfa":
      return <StepUpNotice stepUp={turn.stepUp} reason="Aby kontynuować rozmowę, potwierdź swoją tożsamość drugim składnikiem logowania." />;
    case "error":
      return (
        <p role="alert" className="text-sm text-danger">
          {turn.message}
        </p>
      );
    case "answered":
      return <AnswerCard answer={turn.answer} />;
  }
}

export function AnswerCard({ answer }: { answer: RagAnswer }) {
  return (
    <Card className={cn("flex flex-col gap-4", answer.no_official_position && "border-dashed bg-surface-muted")}>
      {answer.no_official_position ? (
        <div role="note" className="text-sm">
          <p className="font-medium">Brak oficjalnego stanowiska ekspertów</p>
          <p className="mt-1 text-text-muted">{answer.answer_markdown}</p>
        </div>
      ) : (
        <SafeMarkdown markdown={answer.answer_markdown} citations />
      )}
      {answer.citations.length > 0 && (
        <ol className="grid gap-2 sm:grid-cols-2" aria-label="Źródła">
          {answer.citations.map((c, i) => (
            <CitationCard key={c.article_id} citation={c} number={i + 1} />
          ))}
        </ol>
      )}
    </Card>
  );
}

function CitationCard({ citation, number }: { citation: Citation; number: number }) {
  return (
    <li id={`cite-${number}`} className="rounded-lg border border-border p-3 text-xs">
      <Link href={`/wiki/${citation.article_id}`} className="font-medium text-primary-strong underline">
        [{number}] {citation.article_title}
      </Link>
      <p className="mt-1 text-text-muted">
        {citation.last_verified_at ? `Zweryfikowano ${DATE.format(new Date(citation.last_verified_at))}` : "Bez daty weryfikacji"}
      </p>
      {citation.authors.length > 0 && <p className="text-text-muted">Autorzy: {citation.authors.join(", ")}</p>}
      {citation.approving_experts.length > 0 && (
        <p className="text-text-muted">Zatwierdzili: {citation.approving_experts.join(", ")}</p>
      )}
    </li>
  );
}

function LockedNotice() {
  return (
    <div role="alert" className="fixed inset-0 z-40 flex items-center justify-center bg-bg/95 p-6">
      <Card className="max-w-md text-center">
        <h1 className="font-display text-xl font-semibold">Konto zostało zablokowane</h1>
        <p className="mt-2 text-sm text-text-muted">
          Ze względów bezpieczeństwa dostęp do bazy wiedzy został wstrzymany. Skontaktuj się z administratorem spółdzielni,
          aby wyjaśnić sytuację.
        </p>
      </Card>
    </div>
  );
}
