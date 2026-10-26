import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Button } from "@/components/ui/Button";
import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { Dialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Loading } from "@/components/ui/Skeleton";
import { TabPanel, Tabs } from "@/components/ui/Tabs";

const TABS = [
  { key: "a", label: "Pierwsza" },
  { key: "b", label: "Druga" },
  { key: "c", label: "Trzecia" },
] as const;

function TabsDemo() {
  const [tab, setTab] = useState<"a" | "b" | "c">("a");
  return (
    <>
      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Sekcje" idPrefix="demo" />
      {TABS.map((t) => (
        <TabPanel key={t.key} tab={t.key} idPrefix="demo" hidden={t.key !== tab}>
          panel {t.key}
        </TabPanel>
      ))}
    </>
  );
}

describe("Tabs", () => {
  it("links tabs to panels and keeps only the selected one in the tab order", () => {
    render(<TabsDemo />);
    const first = screen.getByRole("tab", { name: "Pierwsza" });
    expect(first).toHaveAttribute("aria-selected", "true");
    expect(first).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: "Druga" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tabpanel", { name: "Pierwsza" })).toHaveTextContent("panel a");
    expect(screen.queryByRole("tabpanel", { name: "Druga" })).not.toBeInTheDocument(); // hidden
  });

  it("moves with the arrow keys, Home and End, wrapping around", async () => {
    render(<TabsDemo />);
    await userEvent.click(screen.getByRole("tab", { name: "Pierwsza" }));
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Trzecia" })).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveTextContent("panel c");
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Pierwsza" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Trzecia" })).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Pierwsza" })).toHaveFocus();
  });
});

describe("DataTable", () => {
  it("keeps table semantics and labels every cell for the phone layout", () => {
    render(
      <DataTable label="Ule">
        <THead>
          <Tr>
            <Th>Nazwa</Th>
            <Th align="right">Ramki</Th>
          </Tr>
        </THead>
        <TBody>
          <Tr selected>
            <Td primary>Ul 1</Td>
            <Td label="Ramki" align="right">
              10
            </Td>
          </Tr>
        </TBody>
      </DataTable>,
    );
    const table = screen.getByRole("table", { name: "Ule" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Nazwa", "Ramki"]);
    const row = within(table).getByRole("row", { name: /Ul 1/ });
    expect(within(row).getByRole("cell", { name: "10" })).toHaveAttribute("data-label", "Ramki");
  });
});

describe("PageHeader, EmptyState, Loading", () => {
  it("renders a level-one heading with its description and actions", () => {
    render(<PageHeader title="Notatnik" description="Tylko dla Ciebie." actions={<Button>Nowa</Button>} back={<a href="#start">← Start</a>} />);
    expect(screen.getByRole("heading", { level: 1, name: "Notatnik" })).toBeInTheDocument();
    expect(screen.getByText("Tylko dla Ciebie.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nowa" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← Start" })).toBeInTheDocument();
  });

  it("shows an empty state with an action, and a loading status", () => {
    render(
      <>
        <EmptyState title="Brak notatek" action={<Button>Utwórz</Button>}>
          Zacznij od pierwszej.
        </EmptyState>
        <Loading />
      </>,
    );
    expect(screen.getByText("Brak notatek")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Utwórz" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Ładowanie…");
  });
});

describe("Button and Dialog", () => {
  it("has an icon size that needs a label, and shows busy state", () => {
    render(
      <>
        <Button size="icon" aria-label="Zamknij">
          ×
        </Button>
        <Button busy>Zapisz</Button>
      </>,
    );
    expect(screen.getByRole("button", { name: "Zamknij" })).toHaveClass("size-10");
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled();
  });

  it("opens as a modal with its title", () => {
    render(
      <Dialog open title="Potwierdź" onClose={() => undefined}>
        treść
      </Dialog>,
    );
    expect(screen.getByRole("dialog", { name: "Potwierdź" })).toHaveTextContent("treść");
  });
});
