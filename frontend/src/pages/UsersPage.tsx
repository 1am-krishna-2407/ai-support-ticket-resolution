import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { useAuth } from "../auth/AuthContext";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState, Panel } from "../components/States";
import { ApiError } from "../services/api";
import {
  createUser,
  listUsers,
  queryKeys,
  updateUser,
  type NewUser,
  type UserChanges,
} from "../services/endpoints";
import type { Role } from "../services/schemas";

const INPUT = "w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm";

function NewUserForm() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<NewUser>({ email: "", name: "", password: "", role: "agent" });
  const mutation = useMutation({
    mutationFn: () => createUser({ ...form, email: form.email.trim(), name: form.name.trim() }),
    onSuccess: () => {
      setForm({ email: "", name: "", password: "", role: "agent" });
      void queryClient.invalidateQueries({ queryKey: queryKeys.users });
    },
  });
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    mutation.mutate();
  };
  return (
    <form onSubmit={onSubmit} aria-label="New user" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
      <label className="text-xs text-slate-600">
        Name
        <input required className={INPUT} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </label>
      <label className="text-xs text-slate-600">
        Email
        <input
          required
          type="email"
          className={INPUT}
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
        />
      </label>
      <label className="text-xs text-slate-600">
        Initial password
        <input
          required
          type="password"
          autoComplete="new-password"
          minLength={12}
          className={INPUT}
          value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })}
        />
      </label>
      <label className="text-xs text-slate-600">
        Role
        <select className={INPUT} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
          <option value="agent">Agent</option>
          <option value="admin">Admin</option>
        </select>
      </label>
      <div className="flex items-end">
        <button
          type="submit"
          disabled={mutation.isPending}
          className="w-full rounded-md bg-blue-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-800 disabled:opacity-60"
        >
          {mutation.isPending ? "Creating…" : "Create user"}
        </button>
      </div>
      {mutation.isError ? (
        <p role="alert" className="text-sm text-red-700 sm:col-span-2 lg:col-span-5">
          {mutation.error instanceof ApiError ? mutation.error.message : "Could not create the user."}
        </p>
      ) : null}
      {mutation.isSuccess ? (
        <p className="text-sm text-green-700 sm:col-span-2 lg:col-span-5">
          User created. Share the initial password with them privately; they can change it under Account.
        </p>
      ) : null}
    </form>
  );
}

export function UsersPage() {
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.users, queryFn: ({ signal }) => listUsers(signal) });
  const update = useMutation({
    mutationFn: ({ id, changes }: { id: string; changes: UserChanges }) => updateUser(id, changes),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.users }),
  });

  const resetPassword = (id: string, email: string) => {
    const password = window.prompt(`New password for ${email} (at least 12 characters). They will be signed out.`);
    if (password) update.mutate({ id, changes: { password } });
  };

  return (
    <>
      <PageHeader
        title="Users"
        description="Create accounts and manage roles. Agents analyse tickets and see their own history; admins also manage users and run ingestion and evaluations."
      />
      <div className="space-y-6">
        <Panel title="Add a user">
          <NewUserForm />
        </Panel>
        {update.isError ? <ErrorState error={update.error} /> : null}
        {query.isPending ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState error={query.error} />
        ) : (
          <section className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="min-w-full text-sm">
              <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2 font-medium">User</th>
                  <th className="px-4 py-2 font-medium">Role</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Analyses</th>
                  <th className="px-4 py-2 font-medium">Last sign-in</th>
                  <th className="px-4 py-2 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {query.data.items.map((u) => {
                  const self = u.id === me?.user.id;
                  return (
                    <tr key={u.id}>
                      <td className="px-4 py-2">
                        <p className="font-medium text-slate-900">
                          {u.name} {self ? <span className="text-xs text-slate-500">(you)</span> : null}
                        </p>
                        <p className="text-xs text-slate-500">{u.email}</p>
                      </td>
                      <td className="px-4 py-2">
                        <select
                          aria-label={`Role for ${u.email}`}
                          value={u.role}
                          disabled={self || update.isPending}
                          onChange={(e) => update.mutate({ id: u.id, changes: { role: e.target.value as Role } })}
                          className="rounded-md border border-slate-300 px-2 py-1 text-sm"
                        >
                          <option value="agent">Agent</option>
                          <option value="admin">Admin</option>
                        </select>
                      </td>
                      <td className="px-4 py-2">
                        <span className={u.is_active ? "text-green-700" : u.last_login_at ? "text-slate-500" : "text-amber-700"}>
                          {u.is_active ? "Active" : u.last_login_at ? "Deactivated" : "Awaiting approval"}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-slate-700">{u.analyses}</td>
                      <td className="whitespace-nowrap px-4 py-2 text-slate-600">
                        {u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "Never"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2">
                        <button
                          type="button"
                          disabled={self || update.isPending}
                          onClick={() => update.mutate({ id: u.id, changes: { is_active: !u.is_active } })}
                          className="mr-2 rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-40"
                        >
                          {u.is_active ? "Deactivate" : u.last_login_at ? "Activate" : "Approve"}
                        </button>
                        <button
                          type="button"
                          disabled={update.isPending}
                          onClick={() => resetPassword(u.id, u.email)}
                          className="rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-40"
                        >
                          Reset password
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
        )}
      </div>
    </>
  );
}
