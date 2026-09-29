import { Link } from "react-router";

import { PageHeader } from "../components/PageHeader";

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" description="There is no page at this address." />
      <Link to="/" className="text-sm font-medium text-blue-700 underline">
        Go to the dashboard
      </Link>
    </>
  );
}
