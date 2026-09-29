import { NavLink, Outlet } from "react-router";

import { NAV_ITEMS } from "../navigation";
import { ApiStatusIndicator } from "./ApiStatusIndicator";

export function AppShell() {
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2"
      >
        Skip to main content
      </a>

      <aside className="border-b border-slate-200 bg-white md:w-60 md:shrink-0 md:border-b-0 md:border-r">
        <div className="px-5 py-4">
          <p className="text-sm font-semibold tracking-tight text-slate-900">
            Support Resolution
          </p>
          <p className="text-xs text-slate-500">AI-assisted ticket drafts</p>
        </div>
        <nav aria-label="Primary">
          <ul className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:overflow-visible">
            {NAV_ITEMS.map((item) => (
              <li key={item.path}>
                <NavLink
                  to={item.path}
                  end={item.path === "/"}
                  className={({ isActive }) =>
                    [
                      "block whitespace-nowrap rounded-md px-3 py-2 text-sm",
                      isActive
                        ? "bg-blue-50 font-medium text-blue-800"
                        : "text-slate-700 hover:bg-slate-100",
                    ].join(" ")
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-end border-b border-slate-200 bg-white px-6 py-3">
          <ApiStatusIndicator />
        </header>
        <main id="main" className="flex-1 px-6 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
