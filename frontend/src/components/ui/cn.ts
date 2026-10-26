import { twMerge } from "tailwind-merge";

/** Joins class names; a later class wins over an earlier one for the same property (12.2). */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return twMerge(...parts);
}
