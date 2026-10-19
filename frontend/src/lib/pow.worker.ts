/// <reference lib="webworker" />
import { solvePow, type PowMessage } from "@/lib/pow-core";

self.onmessage = async (event: MessageEvent<{ nonce: string; difficulty: number }>) => {
  const { nonce, difficulty } = event.data;
  const post = (message: PowMessage) => self.postMessage(message);
  const counter = await solvePow(nonce, difficulty, (tried) => post({ type: "progress", tried }));
  post({ type: "solved", counter });
};
