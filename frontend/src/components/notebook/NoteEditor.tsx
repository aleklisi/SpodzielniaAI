"use client";

import "@blocknote/ariakit/style.css";

import { en, pl } from "@blocknote/core/locales";
import { BlockNoteView } from "@blocknote/ariakit";
import { useCreateBlockNote } from "@blocknote/react";
import { useLocale } from "next-intl";
import { useEffect, useRef } from "react";

import { appLocale } from "@/i18n/locales";

import { useNonce } from "@/lib/nonce";

/**
 * BlockNote editor for a note body. Bodies are stored as Markdown (the backend scrubs PII
 * and promotes plain text), so formatting BlockNote can't express in Markdown is dropped.
 * Loaded on the client only (next/dynamic, ssr: false).
 *
 * The Markdown conversion goes through HTML, which the Trusted Types default policy
 * sanitises (DOMPurify): it runs once typing pauses, not on every keystroke. Chrome reports
 * a CSP violation for each inline style/handler attribute DOMPurify meets while parsing in
 * its inert document; the attribute is never applied and DOMPurify removes it.
 */
const CONVERT_AFTER_MS = 500;
export default function NoteEditor({ markdown, onChange }: { markdown: string; onChange: (markdown: string) => void }) {
  // TipTap and BlockNote inject <style> elements: they need the CSP nonce.
  const nonce = useNonce();
  // The editor's own menus and placeholders follow the UI language (14.3).
  const dictionary = appLocale(useLocale()) === "en" ? en : pl;
  const editor = useCreateBlockNote({ dictionary, _tiptapOptions: { injectNonce: nonce } });
  const loaded = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  }, [onChange]);

  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    const blocks = editor.tryParseMarkdownToBlocks(markdown);
    if (blocks.length) editor.replaceBlocks(editor.document, blocks);
  }, [editor, markdown]);

  // Convert what is pending when the editor goes away (the autosave flushes it after).
  useEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current);
        onChangeRef.current(editor.blocksToMarkdownLossy(editor.document));
      }
    },
    [editor],
  );

  return (
    <div className="min-h-64 rounded-xl border border-border bg-surface py-3">
      <BlockNoteView
        editor={editor}
        theme="light"
        onChange={() => {
          if (!loaded.current) return;
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(() => {
            timer.current = null;
            onChangeRef.current(editor.blocksToMarkdownLossy(editor.document));
          }, CONVERT_AFTER_MS);
        }}
      />
    </div>
  );
}
