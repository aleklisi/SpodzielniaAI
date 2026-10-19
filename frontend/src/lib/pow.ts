/** Solving the shield's proof-of-work off the main thread (`pow.worker.ts`). */

import { solvePow, type PowMessage } from "@/lib/pow-core";

export { leadingZeroBits, solvePow, type PowMessage } from "@/lib/pow-core";

/** Solve in a worker when the browser has them, on this thread otherwise (tests). */
export function solveInBackground(
  nonce: string,
  difficulty: number,
  onProgress: (tried: number) => void,
  signal?: AbortSignal,
): Promise<string> {
  if (typeof Worker === "undefined") return solvePow(nonce, difficulty, onProgress, signal);
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./pow.worker.ts", import.meta.url), { type: "module" });
    signal?.addEventListener("abort", () => {
      worker.terminate();
      reject(new DOMException("aborted", "AbortError"));
    });
    worker.onmessage = (event: MessageEvent<PowMessage>) => {
      if (event.data.type === "progress") onProgress(event.data.tried);
      else {
        worker.terminate();
        resolve(event.data.counter);
      }
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "pow worker failed"));
    };
    worker.postMessage({ nonce, difficulty });
  });
}
