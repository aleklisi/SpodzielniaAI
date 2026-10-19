import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAutosave } from "@/lib/autosave";

describe("useAutosave", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("merges changes and saves once after the pause", async () => {
    const save = vi.fn(async () => {});
    const { result } = renderHook(() => useAutosave(save, 1000));
    act(() => result.current.schedule({ title: "Przegląd" }));
    act(() => result.current.schedule({ body: "Rodzina 3 silna" }));
    expect(result.current.state).toBe("pending");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith({ title: "Przegląd", body: "Rodzina 3 silna" });
    expect(result.current.state).toBe("saved");
  });

  it("flushes a pending change on unmount", () => {
    const save = vi.fn(async () => {});
    const { result, unmount } = renderHook(() => useAutosave(save, 1000));
    act(() => result.current.schedule({ body: "ostatnie słowa" }));
    unmount();
    expect(save).toHaveBeenCalledWith({ body: "ostatnie słowa" });
  });

  it("reports a failed save", async () => {
    const { result } = renderHook(() => useAutosave(async () => Promise.reject(new Error("x")), 10));
    act(() => result.current.schedule({ body: "x" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(result.current.state).toBe("error");
  });
});
