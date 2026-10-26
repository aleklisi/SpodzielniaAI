import {
  Bot,
  BookOpen,
  ClipboardCheck,
  Database,
  FilePen,
  HandHeart,
  Images,
  LockKeyhole,
  MessagesSquare,
  NotebookPen,
  ShieldCheck,
  Tags,
  Users,
  type LucideIcon,
} from "lucide-react";

import { hasRole, type Me, type Role } from "@/lib/session";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
}

export interface NavSection {
  title: string;
  role?: Role;
  items: NavItem[];
}

/** One menu for the sidebar, the rail and the phone drawer (the API enforces the roles regardless). */
export const SECTIONS: NavSection[] = [
  {
    title: "Moja praca",
    items: [
      { href: "/notebook", label: "Notatnik", icon: NotebookPen },
      { href: "/me/drafts", label: "Moje szkice", icon: FilePen },
      { href: "/me/contributions", label: "Moje kontrybucje", icon: HandHeart },
    ],
  },
  {
    title: "Wiedza spółdzielni",
    items: [
      { href: "/wiki", label: "Wiki", icon: BookOpen },
      { href: "/atlas", label: "Atlas", icon: Images },
      { href: "/chat", label: "Zapytaj spółdzielnię", icon: MessagesSquare },
    ],
  },
  {
    title: "Rada Ekspertów",
    role: "EXPERT",
    items: [
      { href: "/review", label: "Recenzje", icon: ClipboardCheck },
      { href: "/review/atlas", label: "Recenzje atlasu", icon: Images },
      { href: "/review/labels", label: "Słownik etykiet", icon: Tags },
    ],
  },
  {
    title: "Administracja",
    role: "ADMIN",
    items: [
      { href: "/admin/security", label: "Bezpieczeństwo", icon: ShieldCheck },
      { href: "/admin/llm", label: "Modele AI", icon: Bot },
      { href: "/admin/users", label: "Użytkownicy", icon: Users },
      { href: "/admin/atlas", label: "Atlas (admin)", icon: Database },
    ],
  },
];

export const PRIVACY_ITEM: NavItem = { href: "/privacy", label: "Twoje dane", icon: LockKeyhole };

export function visibleSections(me: Me | undefined): NavSection[] {
  if (!me) return [];
  return SECTIONS.filter((section) => !section.role || hasRole(me, section.role));
}

/** The longest href that is the path or a parent of it, so `/review/atlas` doesn't also mark `/review`. */
export function activeHref(path: string, hrefs: string[]): string | undefined {
  return hrefs
    .filter((href) => path === href || path.startsWith(`${href}/`))
    .sort((a, b) => b.length - a.length)[0];
}
