/** Polish plural: 1 → one, 2–4 (not 12–14) → few, otherwise many. */
export function plural(n: number, one: string, few: string, many: string): string {
  if (n === 1) return one;
  const tens = n % 100;
  return n % 10 >= 2 && n % 10 <= 4 && (tens < 12 || tens > 14) ? few : many;
}

export const accounts = (n: number) => `${n} ${plural(n, "konto", "konta", "kont")}`;
export const blocks = (n: number) => `${n} ${plural(n, "blok", "bloki", "bloków")}`;
