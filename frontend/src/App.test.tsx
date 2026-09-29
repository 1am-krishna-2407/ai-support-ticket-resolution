import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { createQueryClient } from "./lib/queryClient";
import { NAV_ITEMS } from "./navigation";
import { routes } from "./routes";

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function stubHealth(response: () => Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(response));
}

const ok = () =>
  Promise.resolve(
    new Response(JSON.stringify({ status: "ok" }), {
      headers: { "Content-Type": "application/json" },
    }),
  );

describe("app shell", () => {
  it("renders all seven navigation items", () => {
    stubHealth(ok);
    renderAt("/");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(NAV_ITEMS.map((i) => i.label));
  });

  it.each(NAV_ITEMS)("navigates to $label", async ({ label }) => {
    stubHealth(ok);
    renderAt("/");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    await userEvent.click(within(nav).getByRole("link", { name: label }));
    expect(screen.getByRole("heading", { level: 1, name: label })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
  });

  it("shows a not-found page for unknown routes", () => {
    stubHealth(ok);
    renderAt("/nope");
    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("shows the API as online when /health succeeds", async () => {
    stubHealth(ok);
    renderAt("/");
    expect(await screen.findByText("API online")).toBeInTheDocument();
  });

  it("shows the API as unreachable instead of a blank screen when the backend is down", async () => {
    stubHealth(() => Promise.reject(new TypeError("Failed to fetch")));
    renderAt("/");
    expect(await screen.findByText("API unreachable")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Dashboard" })).toBeInTheDocument();
  });
});
