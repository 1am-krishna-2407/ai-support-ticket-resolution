export interface NavItem {
  path: string;
  label: string;
  /** Shown only to admins. */
  adminOnly?: boolean;
}

/** Primary sections, in navigation order. */
export const NAV_ITEMS: readonly NavItem[] = [
  { path: "/", label: "Dashboard" },
  { path: "/tickets", label: "Ticket Analysis" },
  { path: "/history", label: "History" },
  { path: "/retrieval", label: "Retrieval Explorer" },
  { path: "/knowledge-base", label: "Knowledge Base" },
  { path: "/ingestion", label: "Ingestion" },
  { path: "/evaluation", label: "Evaluation" },
  { path: "/status", label: "System Status" },
  { path: "/users", label: "Users", adminOnly: true },
];

export function navItemsFor(isAdmin: boolean): NavItem[] {
  return NAV_ITEMS.filter((item) => isAdmin || !item.adminOnly);
}
