"use client";

import { useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";

import { cn } from "@/components/ui/cn";
import { clampBox, MIN_SIDE, type Mark } from "@/lib/photos";

/** Box colours cycle through the theme tokens (each works in light and dark mode). */
export const MARK_COLORS = [
  { border: "border-primary", chip: "bg-primary text-primary-contrast" },
  { border: "border-accent", chip: "bg-accent text-white" },
  { border: "border-warn", chip: "bg-warn text-white" },
  { border: "border-ok", chip: "bg-ok text-white" },
  { border: "border-danger", chip: "bg-danger text-white" },
] as const;

export function markColor(index: number) {
  return MARK_COLORS[index % MARK_COLORS.length];
}

type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
const HANDLE_POS: Record<Handle, string> = {
  nw: "left-0 top-0 cursor-nwse-resize",
  n: "left-1/2 top-0 cursor-ns-resize",
  ne: "left-full top-0 cursor-nesw-resize",
  e: "left-full top-1/2 cursor-ew-resize",
  se: "left-full top-full cursor-nwse-resize",
  s: "left-1/2 top-full cursor-ns-resize",
  sw: "left-0 top-full cursor-nesw-resize",
  w: "left-0 top-1/2 cursor-ew-resize",
};

type Drag =
  | { kind: "draw"; key: string; x0: number; y0: number }
  | { kind: "move"; key: string; dx: number; dy: number }
  | { kind: "resize"; key: string; handle: Handle; origin: Mark };

const STEP = 0.01;
const FINE_STEP = 0.002;

let counter = 0;
export function newMarkKey(): string {
  counter += 1;
  return `new-${Date.now().toString(36)}-${counter}`;
}

function resize(origin: Mark, handle: Handle, px: number, py: number) {
  let { x, y, w, h } = origin;
  const right = x + w;
  const bottom = y + h;
  if (handle.includes("w")) {
    x = Math.min(Math.max(px, 0), right - MIN_SIDE);
    w = right - x;
  }
  if (handle.includes("e")) w = Math.max(Math.min(px, 1) - x, MIN_SIDE);
  if (handle.includes("n")) {
    y = Math.min(Math.max(py, 0), bottom - MIN_SIDE);
    h = bottom - y;
  }
  if (handle.includes("s")) h = Math.max(Math.min(py, 1) - y, MIN_SIDE);
  return clampBox({ x, y, w, h });
}

/**
 * The photo with its marks as positioned boxes (percentages of the image, so they stay put at
 * any display size or zoom). Drag on the photo to draw, drag a box to move it, drag a handle
 * to resize. Keyboard: Tab to a box, arrows move it (Shift: finer), Alt+arrows resize,
 * Delete removes, Escape deselects. Pointer events cover mouse, pen and touch.
 */
export function ImageAnnotator({
  src,
  alt,
  aspect,
  marks,
  selected,
  onSelect,
  onChange,
  readOnly = false,
}: {
  src: string;
  alt: string;
  aspect: number | null; // width / height of the image, once known
  marks: Mark[];
  selected: string | null;
  onSelect: (key: string | null) => void;
  onChange: (marks: Mark[]) => void;
  readOnly?: boolean;
}) {
  const surface = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [zoom, setZoom] = useState(1);
  const [natural, setNatural] = useState<number | null>(null);
  const ratio = aspect ?? natural ?? 4 / 3;

  function point(event: { clientX: number; clientY: number }) {
    const rect = surface.current!.getBoundingClientRect();
    return {
      x: Math.min(Math.max((event.clientX - rect.left) / (rect.width || 1), 0), 1),
      y: Math.min(Math.max((event.clientY - rect.top) / (rect.height || 1), 0), 1),
    };
  }

  function update(key: string, box: { x: number; y: number; w: number; h: number }) {
    onChange(marks.map((m) => (m.key === key ? { ...m, ...box } : m)));
  }

  function capture(event: ReactPointerEvent) {
    surface.current?.setPointerCapture?.(event.pointerId);
  }

  function startDraw(event: ReactPointerEvent<HTMLDivElement>) {
    if (readOnly || event.button !== 0 || event.target !== event.currentTarget) return;
    event.preventDefault();
    const p = point(event);
    const key = newMarkKey();
    capture(event);
    onChange([...marks, { key, label: null, x: p.x, y: p.y, w: 0, h: 0, description: "", source: "USER" }]);
    onSelect(key);
    setDrag({ kind: "draw", key, x0: p.x, y0: p.y });
  }

  function startMove(event: ReactPointerEvent, mark: Mark) {
    onSelect(mark.key);
    if (readOnly || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const p = point(event);
    capture(event);
    setDrag({ kind: "move", key: mark.key, dx: p.x - mark.x, dy: p.y - mark.y });
  }

  function startResize(event: ReactPointerEvent, mark: Mark, handle: Handle) {
    if (readOnly || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    capture(event);
    setDrag({ kind: "resize", key: mark.key, handle, origin: mark });
  }

  function onMove(event: ReactPointerEvent) {
    if (!drag) return;
    const p = point(event);
    if (drag.kind === "draw") {
      update(drag.key, {
        x: Math.min(drag.x0, p.x),
        y: Math.min(drag.y0, p.y),
        w: Math.abs(p.x - drag.x0),
        h: Math.abs(p.y - drag.y0),
      });
    } else if (drag.kind === "move") {
      const mark = marks.find((m) => m.key === drag.key);
      if (mark) update(drag.key, clampBox({ x: p.x - drag.dx, y: p.y - drag.dy, w: mark.w, h: mark.h }));
    } else {
      update(drag.key, resize(drag.origin, drag.handle, p.x, p.y));
    }
  }

  function onUp() {
    if (drag?.kind === "draw") {
      const mark = marks.find((m) => m.key === drag.key);
      // A click (or a tiny drag) is not a box.
      if (!mark || mark.w < MIN_SIDE * 2 || mark.h < MIN_SIDE * 2) {
        onChange(marks.filter((m) => m.key !== drag.key));
        onSelect(null);
      }
    }
    setDrag(null);
  }

  function onKey(event: KeyboardEvent, mark: Mark) {
    if (readOnly) return;
    const step = event.shiftKey ? FINE_STEP : STEP;
    const delta: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    if (event.key in delta) {
      event.preventDefault();
      const [dx, dy] = delta[event.key];
      if (event.altKey) update(mark.key, clampBox({ x: mark.x, y: mark.y, w: mark.w + dx, h: mark.h + dy }));
      else update(mark.key, clampBox({ x: mark.x + dx, y: mark.y + dy, w: mark.w, h: mark.h }));
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      onChange(marks.filter((m) => m.key !== mark.key));
      onSelect(null);
    } else if (event.key === "Escape") {
      onSelect(null);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-sm" role="group" aria-label="Powiększenie">
        <button
          type="button"
          className="rounded-md border border-border px-2 py-1 disabled:opacity-50"
          onClick={() => setZoom((z) => Math.max(1, z / 2))}
          disabled={zoom <= 1}
          aria-label="Pomniejsz"
        >
          −
        </button>
        <span aria-live="polite">{Math.round(zoom * 100)}%</span>
        <button
          type="button"
          className="rounded-md border border-border px-2 py-1 disabled:opacity-50"
          onClick={() => setZoom((z) => Math.min(4, z * 2))}
          disabled={zoom >= 4}
          aria-label="Powiększ"
        >
          +
        </button>
        {!readOnly && <span className="text-text-muted">Przeciągnij po zdjęciu, aby zaznaczyć element.</span>}
      </div>
      <div className="max-h-[75vh] overflow-auto rounded-lg border border-border bg-surface-muted">
        <div
          ref={surface}
          data-testid="annotator-surface"
          className={cn("relative touch-none select-none", !readOnly && "cursor-crosshair")}
          style={{ width: `${zoom * 100}%`, aspectRatio: String(ratio) }}
          onPointerDown={startDraw}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={onUp}
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- authenticated same-origin bytes, not a static asset */}
          <img
            src={src}
            alt={alt}
            draggable={false}
            className="pointer-events-none absolute inset-0 h-full w-full"
            onLoad={(e) => {
              const img = e.currentTarget;
              if (img.naturalWidth && img.naturalHeight) setNatural(img.naturalWidth / img.naturalHeight);
            }}
          />
          {marks.map((mark, index) => {
            const color = markColor(index);
            const active = mark.key === selected;
            return (
              <div
                key={mark.key}
                role="button"
                tabIndex={0}
                aria-pressed={active}
                aria-label={`Oznaczenie ${index + 1}: ${mark.label?.name ?? "bez etykiety"}`}
                className={cn(
                  "absolute border-2 outline-none",
                  color.border,
                  active ? "z-10 bg-white/10 ring-2 ring-white/70" : "bg-transparent",
                  !readOnly && "cursor-move",
                )}
                style={{ left: `${mark.x * 100}%`, top: `${mark.y * 100}%`, width: `${mark.w * 100}%`, height: `${mark.h * 100}%` }}
                onPointerDown={(e) => startMove(e, mark)}
                onFocus={() => onSelect(mark.key)}
                onKeyDown={(e) => onKey(e, mark)}
              >
                <span className={cn("absolute -top-5 left-0 whitespace-nowrap rounded px-1 text-xs", color.chip)}>
                  {index + 1}. {mark.label?.name ?? "?"}
                </span>
                {active &&
                  !readOnly &&
                  HANDLES.map((handle) => (
                    <span
                      key={handle}
                      aria-hidden
                      data-handle={handle}
                      className={cn("absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-white bg-text", HANDLE_POS[handle])}
                      onPointerDown={(e) => startResize(e, mark, handle)}
                    />
                  ))}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
