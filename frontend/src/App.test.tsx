import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { createQueryClient } from "./lib/queryClient";
import { navItemsFor } from "./navigation";
import { safeNext } from "./pages/LoginPage";
import { routes } from "./routes";
import { errorBody, stubApi } from "./test/fetchRouter";
import { me } from "./test/fixtures";

const health = { path: "/health", body: { status: "ok" } };
const signedOut = { path: "/auth/me", status: 401, body: errorBody("UNAUTHORIZED", "Sign in to continue.") };

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("app shell", () => {
  it("shows every section to an admin", async () => {
    stubApi([health]);
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(navItemsFor(true).map((i) => i.label));
  });

  it("hides admin-only sections from agents", async () => {
    stubApi([health, { path: "/auth/me", body: me("agent") }]);
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    expect(within(nav).queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "History" })).toBeInTheDocument();
  });

  it("blocks agents who open an admin page directly", async () => {
    stubApi([health, { path: "/auth/me", body: me("agent") }]);
    renderAt("/users");
    expect(await screen.findByRole("heading", { name: "Admins only" })).toBeInTheDocument();
  });

  it.each(navItemsFor(true))("navigates to $label", async ({ label }) => {
    stubApi([health]);
    renderAt("/");
    const nav = await screen.findByRole("navigation", { name: "Primary" });
    await userEvent.click(within(nav).getByRole("link", { name: label }));
    expect(screen.getByRole("heading", { level: 1, name: label })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: label })).toHaveAttribute("aria-current", "page");
  });

  it("shows the signed-in user and their stats at the bottom of the sidebar", async () => {
    stubApi([health]);
    renderAt("/");
    const card = await screen.findByRole("region", { name: "Your profile" });
    expect(within(card).getByText("Ada Admin")).toBeInTheDocument();
    expect(within(card).getByText("admin@example.test")).toBeInTheDocument();
    expect(within(card).getByText("admin")).toBeInTheDocument();
    expect(within(card).getByText("Analyses").previousSibling).toHaveTextContent("7");
    expect(within(card).getByText("Drafts").previousSibling).toHaveTextContent("4");
    expect(within(card).getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("signs out through the API and returns to the login page", async () => {
    const calls = stubApi([health, { method: "POST", path: "/auth/logout", status: 204, body: null }]);
    const router = renderAt("/history");
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/auth/logout"))).toBe(true);
  });

  it("shows a not-found page for unknown routes", async () => {
    stubApi([health]);
    renderAt("/nope");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("shows the API as online when /health succeeds", async () => {
    stubApi([health]);
    renderAt("/");
    expect(await screen.findByText("API online")).toBeInTheDocument();
  });

  it("explains an unreachable backend instead of showing a blank screen", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    renderAt("/");
    expect(await screen.findByRole("alert")).toHaveTextContent(/Could not reach the API/);
  });
});

describe("sign-in", () => {
  it("sends signed-out visitors to the login page and back afterwards", async () => {
    const calls = stubApi([health, signedOut, { method: "POST", path: "/auth/login", body: me("agent") }]);
    const router = renderAt("/history");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.search).toBe("?next=%2Fhistory");

    await userEvent.type(screen.getByLabelText("Email"), "agent@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { level: 1, name: "History" })).toBeInTheDocument();
    const sent = calls.find((c) => c.url.endsWith("/auth/login"));
    expect(sent?.body).toEqual({ email: "agent@example.test", password: "correct horse battery staple" });
  });

  it("shows one generic message for a failed sign-in", async () => {
    stubApi([
      health,
      signedOut,
      {
        method: "POST",
        path: "/auth/login",
        status: 401,
        body: errorBody("UNAUTHORIZED", "The email or password is incorrect, or the account is disabled."),
      },
    ]);
    renderAt("/login");
    await userEvent.type(await screen.findByLabelText("Email"), "x@example.test");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The email or password is incorrect");
  });

  it.each([
    [null, "/"],
    ["/history", "/history"],
    ["//evil.example.com", "/"],
    ["https://evil.example.com", "/"],
    ["/login", "/"],
  ])("only redirects inside the app after sign-in (%s)", (next, expected) => {
    expect(safeNext(next)).toBe(expected);
  });
});

describe("sign-up", () => {
  const config = (registration: "open" | "approval" | "closed") => ({
    path: "/auth/config",
    body: { registration, password_min_length: 12 },
  });

  async function fillSignUp() {
    await userEvent.type(await screen.findByLabelText("Name"), "New Person");
    await userEvent.type(screen.getByLabelText("Email"), "new@example.test");
    await userEvent.type(screen.getByLabelText(/^Password/), "correct horse battery staple");
    await userEvent.type(screen.getByLabelText("Repeat password"), "correct horse battery staple");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
  }

  it("offers sign-up on the login page when it is open", async () => {
    stubApi([health, signedOut, config("open")]);
    renderAt("/login");
    expect(await screen.findByRole("link", { name: "Create an account" })).toHaveAttribute("href", "/register");
  });

  it("hides sign-up when an admin has closed it", async () => {
    stubApi([health, signedOut, config("closed")]);
    renderAt("/login");
    expect(await screen.findByText("Accounts are created by an administrator.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Create an account" })).not.toBeInTheDocument();
  });

  it("creates an agent account and signs straight in", async () => {
    const calls = stubApi([
      health,
      signedOut,
      config("open"),
      { method: "POST", path: "/auth/register", status: 201, body: { status: "active", me: me("agent") } },
    ]);
    renderAt("/register");
    await fillSignUp();
    expect(await screen.findByRole("region", { name: "Your profile" })).toBeInTheDocument();
    const sent = calls.find((c) => c.url.endsWith("/auth/register"));
    expect(sent?.body).toEqual({ email: "new@example.test", name: "New Person", password: "correct horse battery staple" });
  });

  it("explains when an admin must approve the new account", async () => {
    stubApi([
      health,
      signedOut,
      config("approval"),
      { method: "POST", path: "/auth/register", status: 201, body: { status: "pending", me: null } },
    ]);
    renderAt("/register");
    expect(await screen.findByText("New accounts are activated by an administrator.")).toBeInTheDocument();
    await fillSignUp();
    expect(await screen.findByRole("heading", { name: "Account requested" })).toBeInTheDocument();
  });

  it("catches mismatched passwords before calling the API", async () => {
    const calls = stubApi([health, signedOut, config("open")]);
    renderAt("/register");
    await userEvent.type(await screen.findByLabelText("Name"), "New Person");
    await userEvent.type(screen.getByLabelText("Email"), "new@example.test");
    await userEvent.type(screen.getByLabelText(/^Password/), "correct horse battery staple");
    await userEvent.type(screen.getByLabelText("Repeat password"), "something else entirely");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("The passwords do not match.")).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/auth/register"))).toBe(false);
  });

  it("says so when sign-up is closed", async () => {
    stubApi([health, signedOut, config("closed")]);
    renderAt("/register");
    expect(await screen.findByRole("heading", { name: "Sign-up is closed" })).toBeInTheDocument();
  });
});
