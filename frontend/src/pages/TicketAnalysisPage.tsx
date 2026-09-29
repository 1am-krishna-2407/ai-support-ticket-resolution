import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { z } from "zod";

import { PageHeader } from "../components/PageHeader";
import { ReviewBanner } from "../components/ReviewBanner";
import { ATTRIBUTION_NOTE, SourceCard } from "../components/SourceCard";
import { ErrorState, LoadingState, Panel } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { analyzeTicket, type TicketPayload } from "../services/endpoints";
import type { TicketAnalysis } from "../services/schemas";

export const TITLE_MAX = 300;
export const DESCRIPTION_MAX = 20_000;

const FormSchema = z.object({
  title: z.string().trim().min(3, "Title needs at least 3 characters.").max(TITLE_MAX),
  description: z
    .string()
    .trim()
    .min(10, "Description needs at least 10 characters.")
    .max(DESCRIPTION_MAX),
});

type Deployment = "" | "gitlab.com" | "self-managed" | "dedicated";

const RESOLUTION_STATUS_TEXT: Record<string, string> = {
  draft: "Draft resolution",
  insufficient_evidence: "Insufficient documentation",
  withheld: "Draft withheld (failed validation)",
  unavailable: "No draft (service unavailable)",
};

export function TicketAnalysisPage() {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [deployment, setDeployment] = useState<Deployment>("");
  const [gitlabVersion, setGitlabVersion] = useState("");
  const [executor, setExecutor] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const mutation = useMutation({ mutationFn: (payload: TicketPayload) => analyzeTicket(payload) });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const parsed = FormSchema.safeParse({ title, description });
    if (!parsed.success) {
      const errors: Record<string, string> = {};
      for (const issue of parsed.error.issues) errors[String(issue.path[0])] = issue.message;
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    const environment = {
      ...(deployment ? { deployment } : {}),
      ...(gitlabVersion.trim() ? { gitlab_version: gitlabVersion.trim() } : {}),
      ...(executor.trim() ? { runner_executor: executor.trim() } : {}),
    };
    mutation.mutate({
      ...parsed.data,
      environment: Object.keys(environment).length ? environment : null,
    });
  }

  return (
    <>
      <PageHeader
        title="Ticket Analysis"
        description="Submit a support ticket to get an AI-generated draft resolution grounded in the documentation, with citations and validation checks. Drafts are never sent to customers automatically."
      />
      <div className="grid gap-6 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
        <form onSubmit={onSubmit} noValidate className="space-y-4 rounded-lg border border-slate-200 bg-white p-4" aria-label="Ticket">
          <Field
            id="title"
            label="Title"
            count={title.length}
            max={TITLE_MAX}
            error={fieldErrors.title}
          >
            <input
              id="title"
              value={title}
              maxLength={TITLE_MAX}
              onChange={(e) => setTitle(e.target.value)}
              aria-invalid={Boolean(fieldErrors.title)}
              aria-describedby={fieldErrors.title ? "title-error" : undefined}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </Field>
          <Field
            id="description"
            label="Description"
            count={description.length}
            max={DESCRIPTION_MAX}
            error={fieldErrors.description}
          >
            <textarea
              id="description"
              value={description}
              maxLength={DESCRIPTION_MAX}
              rows={9}
              onChange={(e) => setDescription(e.target.value)}
              aria-invalid={Boolean(fieldErrors.description)}
              aria-describedby={fieldErrors.description ? "description-error" : undefined}
              placeholder="What happened, error messages, what you already tried…"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
          </Field>
          <fieldset className="grid gap-3 sm:grid-cols-3">
            <legend className="mb-1 text-sm font-medium text-slate-800">Environment (optional)</legend>
            <label className="text-xs text-slate-600">
              Deployment
              <select
                value={deployment}
                onChange={(e) => setDeployment(e.target.value as Deployment)}
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                <option value="">Not specified</option>
                <option value="gitlab.com">GitLab.com</option>
                <option value="self-managed">Self-managed</option>
                <option value="dedicated">Dedicated</option>
              </select>
            </label>
            <label className="text-xs text-slate-600">
              GitLab version
              <input
                value={gitlabVersion}
                maxLength={50}
                onChange={(e) => setGitlabVersion(e.target.value)}
                placeholder="e.g. 17.4"
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              />
            </label>
            <label className="text-xs text-slate-600">
              Runner executor
              <input
                value={executor}
                maxLength={50}
                onChange={(e) => setExecutor(e.target.value)}
                placeholder="e.g. docker"
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              />
            </label>
          </fieldset>
          <button
            type="submit"
            disabled={mutation.isPending}
            className="w-full rounded-md bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-slate-400"
          >
            {mutation.isPending ? "Analysing…" : "Analyse ticket"}
          </button>
        </form>

        <div aria-live="polite">
          {mutation.isPending ? (
            <LoadingState label="Classifying, searching the documentation, drafting, and validating… this can take up to a minute." />
          ) : mutation.isError ? (
            <ErrorState error={mutation.error} />
          ) : mutation.data ? (
            <AnalysisResult result={mutation.data} />
          ) : (
            <p className="rounded-lg border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-600">
              Results appear here after you submit a ticket.
            </p>
          )}
        </div>
      </div>
    </>
  );
}

function Field(props: {
  id: string;
  label: string;
  count: number;
  max: number;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <label htmlFor={props.id} className="text-sm font-medium text-slate-800">
          {props.label}
        </label>
        <span className="text-xs text-slate-500" aria-live="polite">
          {props.count.toLocaleString()} / {props.max.toLocaleString()}
        </span>
      </div>
      {props.children}
      {props.error ? (
        <p id={`${props.id}-error`} className="mt-1 text-xs text-red-700">
          {props.error}
        </p>
      ) : null}
    </div>
  );
}

export function AnalysisResult({ result }: { result: TicketAnalysis }) {
  const { resolution, classification, priority, grounding, retrieval } = result;
  return (
    <div className="space-y-4">
      <ReviewBanner label={result.label} required={result.human_review.required} reasons={result.human_review.reasons} />

      {result.errors.length ? (
        <section role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900">
          <p className="font-medium">Some stages failed; the result below reflects that:</p>
          <ul className="mt-1 list-disc pl-5">
            {result.errors.map((e) => (
              <li key={`${e.stage}-${e.code}`}>
                <span className="font-mono">{e.stage}</span>: {e.message} ({e.code})
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <Panel
        title="Resolution"
        actions={<StatusBadge value={resolution.status} label={RESOLUTION_STATUS_TEXT[resolution.status]} />}
      >
        <p className="text-sm text-slate-800">{resolution.summary}</p>
        {resolution.steps.length ? (
          <ol className="mt-3 space-y-2">
            {resolution.steps.map((step) => (
              <li key={step.number} className="flex gap-3 text-sm">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-blue-100 text-xs font-semibold text-blue-800">
                  {step.number}
                </span>
                <div>
                  <p className="whitespace-pre-line text-slate-900">{step.instruction}</p>
                  <p className="mt-0.5 text-xs text-slate-500">
                    Sources:{" "}
                    {step.source_ids.map((id) => (
                      <a key={id} href={`#src-${id}`} className="mr-1 font-mono text-blue-700 underline">
                        {id}
                      </a>
                    ))}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        ) : null}
        {resolution.explanation ? (
          <p className="mt-3 text-sm text-slate-700">{resolution.explanation}</p>
        ) : null}
        {resolution.warnings.length ? (
          <div className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <p className="font-medium">Warnings</p>
            <ul className="list-disc pl-5">
              {resolution.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {resolution.questions_for_customer.length ? (
          <div className="mt-3 text-sm">
            <p className="font-medium text-slate-800">Questions for the customer</p>
            <ul className="list-disc pl-5 text-slate-700">
              {resolution.questions_for_customer.map((q) => (
                <li key={q}>{q}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {resolution.confidence ? (
          <p className="mt-3 text-xs text-slate-500">Model-stated confidence: {resolution.confidence}</p>
        ) : null}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Classification">
          {classification.category ? (
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-slate-500">Category</dt>
              <dd className="font-medium">{classification.category.replace(/_/g, " ")}</dd>
              <dt className="text-slate-500">Confidence</dt>
              <dd>{classification.confidence?.toFixed(2)}</dd>
              {classification.secondary_category ? (
                <>
                  <dt className="text-slate-500">Also possible</dt>
                  <dd>
                    {classification.secondary_category.replace(/_/g, " ")} (
                    {classification.secondary_confidence?.toFixed(2)})
                  </dd>
                </>
              ) : null}
              {classification.ambiguous ? (
                <>
                  <dt className="text-slate-500">Note</dt>
                  <dd className="text-amber-800">Ambiguous classification</dd>
                </>
              ) : null}
            </dl>
          ) : (
            <p className="text-sm text-slate-600">Classification unavailable.</p>
          )}
          {classification.rationale ? (
            <p className="mt-2 text-xs text-slate-600">{classification.rationale}</p>
          ) : null}
        </Panel>

        <Panel title="Priority (AI-assisted estimate)">
          {priority.level ? (
            <>
              <StatusBadge value={priority.level} />
              <p className="mt-2 text-sm text-slate-700">{priority.explanation}</p>
              <p className="mt-2 text-xs text-slate-500">
                Estimated from the ticket text with a fixed rubric; confirm against your own triage policy.
              </p>
            </>
          ) : (
            <p className="text-sm text-slate-600">{priority.explanation}</p>
          )}
        </Panel>
      </div>

      <Panel
        title="Grounding checks"
        actions={
          <span className="text-xs text-slate-600">
            {grounding.grounded ? "Grounded" : "Not grounded"} · score {grounding.score.toFixed(2)}
            {grounding.claim_level ? " · claim-level checked" : ""}
          </span>
        }
      >
        <ul className="space-y-1 text-sm">
          {grounding.checks.map((c) => (
            <li key={c.name} className="flex gap-2">
              <span aria-hidden="true" className={c.passed ? "text-emerald-700" : "text-red-700"}>
                {c.passed ? "✓" : "✗"}
              </span>
              <span className="sr-only">{c.passed ? "Passed:" : "Failed:"}</span>
              <span>
                <span className="font-mono text-xs text-slate-700">{c.name}</span> {c.detail}
              </span>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title={`Sources (${result.sources.length})`}>
        {result.sources.length ? (
          <div className="space-y-2">
            {result.sources.map((s) => (
              <SourceCard key={s.source_id} source={s} />
            ))}
            <p className="text-xs text-slate-500">{ATTRIBUTION_NOTE}</p>
          </div>
        ) : (
          <p className="text-sm text-slate-600">No documentation met the relevance threshold.</p>
        )}
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Retrieval">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-slate-500">Evidence</dt>
            <dd>{retrieval.evidence_sufficient ? "Sufficient" : "Insufficient"}</dd>
            <dt className="text-slate-500">Reason</dt>
            <dd>{retrieval.reason}</dd>
            <dt className="text-slate-500">Used / candidates</dt>
            <dd>
              {retrieval.evidence_used} / {retrieval.candidates}
            </dd>
            <dt className="text-slate-500">Threshold</dt>
            <dd>{retrieval.threshold}</dd>
          </dl>
          {retrieval.degraded || retrieval.warnings.length ? (
            <ul className="mt-2 list-disc pl-5 text-xs text-amber-800">
              {retrieval.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          ) : null}
          {retrieval.excluded_for_injection.length ? (
            <p className="mt-2 text-xs text-amber-800">
              Excluded as possible prompt injection: {retrieval.excluded_for_injection.join(", ")}
            </p>
          ) : null}
        </Panel>
        <Panel title="Timings">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 font-mono text-xs">
            {Object.entries(result.timings_ms).map(([stage, ms]) => (
              <div key={stage} className="contents">
                <dt className="text-slate-500">{stage}</dt>
                <dd>{Math.round(ms).toLocaleString()} ms</dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-slate-500">
            LLM {result.model.llm ?? "not configured"} · embeddings {result.model.embedding ?? "not configured"}
          </p>
        </Panel>
      </div>
    </div>
  );
}
