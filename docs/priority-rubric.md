# Priority rubric

Priority is an **AI-assisted estimate**. The language model only *extracts signals* from
the ticket text (triage prompt `classification_v1`). A deterministic function
(`backend/app/services/analysis/priority.py`) maps those signals to a level, so the same
signals always give the same priority and every decision lists the factors that triggered it.

## The eight signals

| Signal | Values | Meaning |
|---|---|---|
| `production_impact` | `none` / `degraded` / `outage` | Effect on the customer's production systems |
| `users_affected` | `single_user` / `team` / `many_users` / `all_users` | How many people are affected |
| `security_concern` | yes/no | Possible vulnerability, exposed secret, unauthorised access, compromised account |
| `data_loss_risk` | yes/no | Data has been, or could be, lost or corrupted |
| `blocks_ci_cd_or_deployments` | yes/no | Pipelines, jobs, or deployments are blocked |
| `workaround_available` | yes/no | The ticket says a workaround exists or is in use |
| `time_sensitive` | yes/no | A deadline, release, or urgent timeline is mentioned |
| `regression` | yes/no | Something that previously worked has stopped working |

The prompt tells the model not to assume facts the ticket does not state.

## Rules

The highest level with at least one triggered rule wins. All triggered rules at that level
are reported.

| Level | Triggered when |
|---|---|
| **Critical** | production outage affecting many or all users; security concern with production impact (degraded or outage); risk of data loss during a production outage |
| **High** | production outage; security concern; risk of data loss; CI/CD or deployments blocked with no workaround; degraded service for many users with no workaround |
| **Medium** | degraded production service; CI/CD or deployments blocked (workaround exists); affects a team or more; time-sensitive; regression |
| **Low** | none of the above (single user, no production impact) |

## How it is used

- The API returns `priority.level`, `priority.triggered_factors`, `priority.explanation`,
  the raw `signals`, and `is_estimate: true`. The UI labels it as an AI-assisted estimate.
- `high` and `critical` add the `high_or_critical_priority` human-review reason; a security
  concern or data-loss risk adds its own reason, whatever the level.
- If triage fails (LLM unavailable or invalid output), the level is `null` and the ticket
  requires review (`classification_unavailable`).

Every rule is covered by a table-driven test (`backend/tests/unit/test_analysis_rules.py`).
