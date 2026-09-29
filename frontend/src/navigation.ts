export interface NavItem {
  path: string;
  label: string;
}

/** The seven primary sections, in navigation order. */
export const NAV_ITEMS: readonly NavItem[] = [
  { path: "/", label: "Dashboard" },
  { path: "/tickets", label: "Ticket Analysis" },
  { path: "/retrieval", label: "Retrieval Explorer" },
  { path: "/knowledge-base", label: "Knowledge Base" },
  { path: "/ingestion", label: "Ingestion" },
  { path: "/evaluation", label: "Evaluation" },
  { path: "/status", label: "System Status" },
];
