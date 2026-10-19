/**
 * Proof-of-work for the shield's step-up (backend 5.6): find `counter` such that
 * SHA-256(`${nonce}:${counter}`) starts with `difficulty` zero bits. Same rule as the server
 * (`shield/challenge.py`). Imported by the worker and by `pow.ts`, and by nothing that
 * creates the worker (a cycle there sends the bundler into a loop).
 */

export function leadingZeroBits(digest: Uint8Array): number {
  let bits = 0;
  for (const byte of digest) {
    if (byte === 0) {
      bits += 8;
      continue;
    }
    return bits + Math.clz32(byte) - 24;
  }
  return bits;
}

const encoder = new TextEncoder();

export async function solvePow(
  nonce: string,
  difficulty: number,
  onProgress?: (tried: number) => void,
  signal?: AbortSignal,
): Promise<string> {
  for (let counter = 0; ; counter++) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", encoder.encode(`${nonce}:${counter}`)));
    if (leadingZeroBits(digest) >= difficulty) return String(counter);
    if (counter % 4096 === 0) onProgress?.(counter);
  }
}

export type PowMessage = { type: "progress"; tried: number } | { type: "solved"; counter: string };
