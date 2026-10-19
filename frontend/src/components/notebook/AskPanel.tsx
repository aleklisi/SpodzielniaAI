"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";

import { SafeMarkdown } from "@/components/markdown/SafeMarkdown";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { useAskNotebook } from "@/lib/notebook";

/** "Zapytaj mój notatnik": private RAG over the member's own notes (5.1), local model only. */
export function AskPanel() {
  const [question, setQuestion] = useState("");
  const ask = useAskNotebook();

  function submit(event: FormEvent) {
    event.preventDefault();
    if (question.trim().length >= 3) ask.mutate(question.trim());
  }

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="font-display text-lg font-semibold">Zapytaj swój notatnik</h2>
      <form onSubmit={submit} className="flex flex-col gap-2">
        <label className="sr-only" htmlFor="ask-notebook">
          Pytanie do notatnika
        </label>
        <textarea
          id="ask-notebook"
          rows={3}
          maxLength={1000}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Np. kiedy ostatnio leczyłem rodzinę nr 3?"
          className="rounded-lg border border-border bg-surface px-3 py-2 text-sm"
        />
        <Button type="submit" busy={ask.isPending} disabled={question.trim().length < 3}>
          Zapytaj
        </Button>
      </form>
      {ask.isError && <p role="alert" className="text-sm text-danger">Nie udało się uzyskać odpowiedzi.</p>}
      {ask.data && (
        <div className="flex flex-col gap-2 text-sm" aria-live="polite">
          {ask.data.no_answer ? (
            <p className="italic text-text-muted">{ask.data.answer_markdown}</p>
          ) : (
            <SafeMarkdown markdown={ask.data.answer_markdown} citations />
          )}
          {ask.data.citations.length > 0 && (
            <ol className="list-decimal pl-5">
              {ask.data.citations.map((c, i) => (
                <li key={c.note_id} id={`cite-${i + 1}`}>
                  <Link href={`/notebook/${c.note_id}`} className="text-primary-strong underline">
                    {c.note_title}
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </Card>
  );
}
