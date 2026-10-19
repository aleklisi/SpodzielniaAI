import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";

import { leadingZeroBits, solvePow } from "@/lib/pow";

describe("proof of work", () => {
  it("counts leading zero bits like the server", () => {
    expect(leadingZeroBits(new Uint8Array([0, 0, 0x80]))).toBe(16);
    expect(leadingZeroBits(new Uint8Array([0, 0x0f]))).toBe(12);
    expect(leadingZeroBits(new Uint8Array([0xff]))).toBe(0);
  });

  it("finds a counter the server accepts", async () => {
    const counter = await solvePow("ab12cd", 10);
    const digest = createHash("sha256").update(`ab12cd:${counter}`).digest();
    expect(leadingZeroBits(new Uint8Array(digest))).toBeGreaterThanOrEqual(10);
  });

  it("stops when aborted", async () => {
    const abort = new AbortController();
    abort.abort();
    await expect(solvePow("n", 30, undefined, abort.signal)).rejects.toThrow("aborted");
  });
});
