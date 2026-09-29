"""Ticket analysis orchestration.

validate → normalise → triage (classification + priority signals) → build query →
retrieve → threshold → generate → validate citations → validate grounding →
decide human review → assemble response.

Failures degrade to an honest draft rather than an error: if the LLM, embeddings, or
retrieval fail, the response says which stage failed and requires human review. Nothing
is generated when evidence is insufficient, and generated steps that fail validation
are withheld.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.categories import Category
from app.core.config import Settings
from app.core.errors import (
    AppError,
    EmbeddingProviderError,
    InvalidModelOutputError,
    LLMProviderError,
    RetrievalUnavailableError,
)
from app.providers.llm import ChatMessage, LLMProvider, strict_json_schema
from app.schemas.analysis import (
    Citation,
    ClaimCheckOutput,
    ClassificationView,
    ClassifyResponse,
    GeneratedResolution,
    GroundingCheck,
    GroundingReport,
    ModelInfo,
    PriorityView,
    ResolutionStatus,
    ResolutionStepView,
    ResolutionView,
    RetrievalSummary,
    StageError,
    TicketAnalysisResponse,
    TriageOutput,
)
from app.schemas.retrieval import RetrievalMethod, RetrievalResult, RetrievedChunk
from app.schemas.tickets import TicketInput
from app.services.analysis.context import (
    build_evidence_context,
    looks_like_injection,
    render_ticket,
)
from app.services.analysis.priority import assess_priority
from app.services.analysis.prompts import PromptRegistry
from app.services.analysis.validation import (
    GroundingInputs,
    ReviewInputs,
    check_grounding,
    decide_human_review,
    validate_citations,
)
from app.services.retrieval.query import transform_query
from app.services.retrieval.service import RetrievalService

T = TypeVar("T", bound=BaseModel)

INSUFFICIENT_EVIDENCE_MESSAGE = (
    "The knowledge base does not contain enough relevant documentation to draft a reliable "
    "resolution for this ticket. A support engineer should review it."
)
WITHHELD_MESSAGE = (
    "A draft was generated but failed automated validation (see grounding checks), so its "
    "steps are withheld. A support engineer should review this ticket."
)
UNAVAILABLE_MESSAGE = (
    "A draft could not be generated because a required service was unavailable. "
    "A support engineer should review this ticket."
)
CATEGORY_DESCRIPTIONS: dict[Category, str] = {
    Category.AUTHENTICATION: "sign-in, tokens, 2FA, SSO/SAML/LDAP, SSH keys",
    Category.API: "REST or GraphQL API usage and errors",
    Category.CI_CD: "pipelines, jobs, .gitlab-ci.yml, variables, artifacts, caching",
    Category.RUNNERS: "GitLab Runner installation, registration, executors, runner errors",
    Category.PROJECTS: "repositories, merge requests, project settings, mirroring, imports",
    Category.PERMISSIONS: "roles, membership, protected branches/tags, approvals",
    Category.INTEGRATIONS: "Jira, Slack, and other third-party integrations",
    Category.WEBHOOKS: "project/group/system hooks, webhook delivery",
    Category.TROUBLESHOOTING: "general errors or failures not specific to one area",
    Category.SECURITY: "vulnerabilities, scanners, secret detection, security settings",
    Category.CONFIGURATION: "instance administration and self-managed configuration",
}


@dataclass
class TriageResult:
    output: TriageOutput | None
    classification: ClassificationView
    priority: PriorityView
    errors: list[StageError]


def _error(stage: str, exc: Exception) -> StageError:
    code = exc.code.value if isinstance(exc, AppError) else "INTERNAL_ERROR"
    message = exc.message if isinstance(exc, AppError) else "Unexpected error."
    return StageError(stage=stage, code=code, message=message)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


class TicketAnalysisService:
    def __init__(
        self,
        *,
        settings: Settings,
        llm: LLMProvider | None,
        retrieval: RetrievalService,
        prompts: PromptRegistry,
        count_tokens: Callable[[str], int],
        embedding_spec: str | None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._retrieval = retrieval
        self._prompts = prompts
        self._count = count_tokens
        self._embedding_spec = embedding_spec

    # --- structured LLM calls ----------------------------------------------------

    async def _structured(
        self, model: type[T], schema_name: str, messages: list[ChatMessage], max_tokens: int
    ) -> T:
        """One call, plus at most one correction attempt when the output fails validation."""
        if self._llm is None:
            raise LLMProviderError("The language model provider is not configured.")
        schema = strict_json_schema(model)
        conversation = list(messages)
        for attempt in range(2):
            try:
                result = await self._llm.complete_json(
                    conversation, schema_name=schema_name, schema=schema, max_tokens=max_tokens
                )
                return model.model_validate_json(result.content)
            except ValidationError as exc:
                problem = "; ".join(
                    f"{'.'.join(map(str, e['loc'])) or 'root'}: {e['msg']}"
                    for e in exc.errors()[:8]
                )
                content = result.content
            except InvalidModelOutputError as exc:
                problem, content = exc.message, ""
            if attempt == 1:
                raise InvalidModelOutputError(
                    "The model output failed validation after one correction attempt.",
                    details={"schema": schema_name, "problem": problem[:500]},
                )
            if content:
                conversation.append(ChatMessage("assistant", content))
            conversation.append(
                ChatMessage(
                    "user",
                    f"Your previous response was invalid ({problem[:500]}). "
                    "Return a corrected JSON object that matches the schema, and nothing else.",
                )
            )
        raise AssertionError("unreachable")  # pragma: no cover

    def _system(self) -> ChatMessage:
        return ChatMessage("system", self._prompts.get("system").render())

    # --- triage --------------------------------------------------------------------

    async def triage(self, ticket: TicketInput) -> TriageResult:
        ticket_text = render_ticket(
            ticket.title,
            ticket.description,
            ticket.environment.model_dump() if ticket.environment else None,
        )
        categories = "\n".join(f"- {c.value}: {d}" for c, d in CATEGORY_DESCRIPTIONS.items())
        prompt = self._prompts.get("classification").render(
            categories=categories, ticket=ticket_text
        )
        try:
            out = await self._structured(
                TriageOutput, "triage", [self._system(), ChatMessage("user", prompt)], 1200
            )
        except (LLMProviderError, InvalidModelOutputError) as exc:
            return TriageResult(
                output=None,
                classification=ClassificationView(
                    category=None,
                    confidence=None,
                    secondary_category=None,
                    secondary_confidence=None,
                    ambiguous=False,
                    rationale=None,
                ),
                priority=PriorityView(
                    level=None,
                    explanation="Priority could not be estimated because triage failed.",
                    triggered_factors=[],
                    signals=None,
                ),
                errors=[_error("triage", exc)],
            )
        s = self._settings
        secondary = out.secondary_category if out.secondary_category != out.category else None
        ambiguous = secondary is not None and (
            out.confidence - out.secondary_confidence < s.classification_ambiguity_margin
        )
        decision = assess_priority(out.signals)
        return TriageResult(
            output=out,
            classification=ClassificationView(
                category=out.category,
                confidence=round(out.confidence, 3),
                secondary_category=secondary,
                secondary_confidence=round(out.secondary_confidence, 3) if secondary else None,
                ambiguous=ambiguous,
                rationale=out.rationale,
            ),
            priority=PriorityView(
                level=decision.level,
                explanation=decision.explanation,
                triggered_factors=list(decision.triggered),
                signals=out.signals,
            ),
            errors=[],
        )

    async def classify(self, ticket: TicketInput) -> ClassifyResponse:
        started = time.perf_counter()
        triage = await self.triage(ticket)
        elapsed = _ms(started)
        review = decide_human_review(self._review_inputs(ticket, triage, None))
        return ClassifyResponse(
            classification=triage.classification,
            priority=triage.priority,
            human_review=review,
            errors=triage.errors,
            timings_ms={"classification": elapsed, "total": elapsed},
            model=self._model_info(),
        )

    # --- full analysis --------------------------------------------------------------

    async def analyze(
        self, ticket: TicketInput, *, precomputed_retrieval: RetrievalResult | None = None
    ) -> TicketAnalysisResponse:
        """Analyse a ticket. `precomputed_retrieval` lets callers that already retrieved
        (the evaluation runner) avoid a second retrieval and its embedding call."""
        total_started = time.perf_counter()
        timings: dict[str, float] = {}
        errors: list[StageError] = []

        started = time.perf_counter()
        triage = await self.triage(ticket)
        timings["classification"] = _ms(started)
        errors.extend(triage.errors)

        started = time.perf_counter()
        retrieval: RetrievalResult | None = precomputed_retrieval
        if retrieval is None:
            try:
                query = transform_query(
                    ticket.title,
                    ticket.description,
                    max_chars=self._settings.retrieval_max_query_chars,
                )
                retrieval = await self._retrieval.retrieve(query, method=RetrievalMethod.HYBRID)
            except (RetrievalUnavailableError, EmbeddingProviderError) as exc:
                errors.append(_error("retrieval", exc))
        timings["retrieval"] = _ms(started)
        if retrieval is not None and "embedding" in retrieval.diagnostics.timings_ms:
            timings["embedding"] = retrieval.diagnostics.timings_ms["embedding"]

        evidence = [r for r in retrieval.results if r.passes_threshold] if retrieval else []
        sufficient = bool(retrieval and retrieval.evidence_sufficient and evidence)
        context = build_evidence_context(
            evidence, max_tokens=self._settings.context_max_tokens, count_tokens=self._count
        )
        ticket_text = render_ticket(
            ticket.title,
            ticket.description,
            ticket.environment.model_dump() if ticket.environment else None,
        )

        generated: GeneratedResolution | None = None
        invalid_output = llm_failed = False
        if sufficient and context.used:
            started = time.perf_counter()
            prompt = self._prompts.get("ticket_analysis").render(
                ticket=ticket_text, sources=context.text
            )
            try:
                generated = await self._structured(
                    GeneratedResolution,
                    "resolution",
                    [self._system(), ChatMessage("user", prompt)],
                    self._settings.llm_max_output_tokens,
                )
            except InvalidModelOutputError as exc:
                invalid_output = True
                errors.append(_error("generation", exc))
            except LLMProviderError as exc:
                llm_failed = True
                errors.append(_error("generation", exc))
            timings["generation"] = _ms(started)

        started = time.perf_counter()
        resolution, sources, grounding, citations_valid, unsupported = await self._validate(
            generated,
            context_used=list(context.used),
            evidence_sufficient=sufficient,
            ticket_text=ticket_text,
            retrieval_available=retrieval is not None,
            llm_failed=llm_failed or invalid_output,
            errors=errors,
        )
        timings["validation"] = _ms(started)

        review = decide_human_review(
            self._review_inputs(
                ticket,
                triage,
                ReviewInputsPart(
                    evidence_sufficient=sufficient,
                    retrieval_available=retrieval is not None,
                    grounded=grounding.grounded if generated is not None else None,
                    citations_valid=citations_valid,
                    unsupported_identifiers=bool(unsupported),
                    invalid_model_output=invalid_output,
                    llm_unavailable=llm_failed,
                    questions=bool(generated and generated.questions_for_customer),
                    injection_in_evidence=bool(context.excluded_for_injection),
                    retrieval_degraded=bool(retrieval and retrieval.degraded),
                ),
            )
        )
        timings["total"] = _ms(total_started)
        return TicketAnalysisResponse(
            classification=triage.classification,
            priority=triage.priority,
            resolution=resolution,
            sources=sources,
            grounding=grounding,
            human_review=review,
            retrieval=RetrievalSummary(
                method=RetrievalMethod.HYBRID,
                evidence_sufficient=sufficient,
                reason=retrieval.reason if retrieval else "Retrieval was unavailable.",
                threshold=self._settings.retrieval_relevance_threshold,
                candidates=len(retrieval.results) if retrieval else 0,
                evidence_used=len(context.used),
                excluded_for_injection=list(context.excluded_for_injection),
                degraded=bool(retrieval and retrieval.degraded),
                warnings=list(retrieval.warnings) if retrieval else [],
            ),
            errors=errors,
            timings_ms=timings,
            model=self._model_info(),
        )

    async def _validate(
        self,
        generated: GeneratedResolution | None,
        *,
        context_used: list[RetrievedChunk],
        evidence_sufficient: bool,
        ticket_text: str,
        retrieval_available: bool,
        llm_failed: bool,
        errors: list[StageError],
    ) -> tuple[ResolutionView, list[Citation], GroundingReport, bool | None, list[str]]:
        if generated is None:
            status: ResolutionStatus = (
                "unavailable"
                if (llm_failed or not retrieval_available)
                else "insufficient_evidence"
            )
            message = (
                UNAVAILABLE_MESSAGE if status == "unavailable" else INSUFFICIENT_EVIDENCE_MESSAGE
            )
            report = GroundingReport(
                grounded=False,
                score=0.0,
                checks=[
                    GroundingCheck(
                        name="evidence_present",
                        passed=bool(context_used),
                        detail=f"{len(context_used)} source(s) met the relevance threshold.",
                    ),
                    GroundingCheck(
                        name="retrieval_threshold",
                        passed=evidence_sufficient,
                        detail="No draft was generated.",
                    ),
                ],
                claim_level=False,
            )
            return (
                self._empty_resolution(status, message),
                self._citations(context_used, None),
                report,
                None,
                [],
            )

        citations = validate_citations(generated, [c.source_id for c in context_used])
        verdicts = await self._claim_check(generated, context_used, errors)
        inputs = GroundingInputs(
            resolution=generated,
            evidence=context_used,
            evidence_sufficient=evidence_sufficient,
            citations=citations,
            ticket_text=ticket_text,
            claim_verdicts=verdicts,
        )
        report = check_grounding(inputs)
        unsupported = list(inputs.unsupported_identifiers)

        if generated.answer_type == "insufficient_evidence":
            view = self._empty_resolution(
                "insufficient_evidence", generated.summary or INSUFFICIENT_EVIDENCE_MESSAGE
            )
            view.questions_for_customer = generated.questions_for_customer
            return view, self._citations(context_used, None), report, citations.valid, unsupported
        if not report.grounded:
            view = self._empty_resolution("withheld", WITHHELD_MESSAGE)
            return view, self._citations(context_used, None), report, citations.valid, unsupported

        view = ResolutionView(
            status="draft",
            summary=generated.summary,
            steps=[
                ResolutionStepView(
                    number=n,
                    instruction=step.instruction,
                    source_ids=[i for i in step.source_ids if i in citations.cited],
                )
                for n, step in enumerate(generated.resolution_steps, 1)
            ],
            explanation=generated.explanation,
            warnings=generated.warnings
            + (
                ["Confidence lowered to medium: the cited evidence does not support 'high'."]
                if inputs.adjusted_confidence
                else []
            ),
            questions_for_customer=generated.questions_for_customer,
            confidence=inputs.adjusted_confidence or generated.confidence,
        )
        return view, self._citations(context_used, set(citations.cited)), report, True, unsupported

    async def _claim_check(
        self,
        generated: GeneratedResolution,
        context_used: Sequence[RetrievedChunk],
        errors: list[StageError],
    ) -> dict[int, bool] | None:
        if not self._settings.claim_level_grounding_enabled or not generated.resolution_steps:
            return None
        steps = "\n".join(
            f"{n}. {s.instruction} [cites: {', '.join(s.source_ids)}]"
            for n, s in enumerate(generated.resolution_steps, 1)
        )
        sources = build_evidence_context(
            context_used, max_tokens=self._settings.context_max_tokens, count_tokens=self._count
        ).text
        prompt = self._prompts.get("grounding").render(steps=steps, sources=sources)
        try:
            out = await self._structured(
                ClaimCheckOutput, "claim_check", [self._system(), ChatMessage("user", prompt)], 1200
            )
        except (LLMProviderError, InvalidModelOutputError) as exc:
            errors.append(_error("claim_check", exc))
            return None
        verdicts = {v.step_number: v.supported for v in out.verdicts}
        # A step the checker skipped counts as unsupported.
        return {n: verdicts.get(n, False) for n in range(1, len(generated.resolution_steps) + 1)}

    @staticmethod
    def _empty_resolution(status: ResolutionStatus, message: str) -> ResolutionView:
        return ResolutionView(
            status=status,
            summary=message,
            steps=[],
            explanation="",
            warnings=[],
            questions_for_customer=[],
            confidence=None,
        )

    @staticmethod
    def _citations(chunks: Sequence[RetrievedChunk], only: set[str] | None) -> list[Citation]:
        return [
            Citation(
                source_id=c.source_id,
                title=c.title,
                section=c.section,
                url=c.url,
                excerpt=c.content[:400],
                semantic_score=c.scores.semantic,
                final_score=c.scores.final,
            )
            for c in chunks
            if only is None or c.source_id in only
        ]

    def _review_inputs(
        self,
        ticket: TicketInput,
        triage: TriageResult,
        part: ReviewInputsPart | None,
    ) -> ReviewInputs:
        signals = triage.output.signals if triage.output else None
        injection = looks_like_injection(f"{ticket.title}\n{ticket.description}")
        return ReviewInputs(
            evidence_sufficient=part.evidence_sufficient if part else True,
            retrieval_available=part.retrieval_available if part else True,
            retrieval_degraded=part.retrieval_degraded if part else False,
            grounded=part.grounded if part else None,
            citations_valid=part.citations_valid if part else None,
            unsupported_identifiers=part.unsupported_identifiers if part else False,
            invalid_model_output=part.invalid_model_output if part else False,
            llm_unavailable=part.llm_unavailable if part else False,
            classification_available=triage.output is not None,
            classification_confidence=triage.classification.confidence,
            ambiguous=triage.classification.ambiguous,
            min_confidence=self._settings.classification_min_confidence,
            priority=triage.priority.level,
            security_concern=bool(signals and signals.security_concern),
            data_loss_risk=bool(signals and signals.data_loss_risk),
            questions_for_customer=part.questions if part else False,
            injection_suspected=injection or bool(part and part.injection_in_evidence),
        )

    def _model_info(self) -> ModelInfo:
        return ModelInfo(
            llm=self._llm.model if self._llm else None,
            embedding=self._embedding_spec,
            prompt_versions=self._prompts.versions(),
        )


@dataclass(frozen=True)
class ReviewInputsPart:
    evidence_sufficient: bool
    retrieval_available: bool
    grounded: bool | None
    citations_valid: bool | None
    unsupported_identifiers: bool
    invalid_model_output: bool
    llm_unavailable: bool
    questions: bool
    injection_in_evidence: bool
    retrieval_degraded: bool = False
