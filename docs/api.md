# API reference

Generated from the application by `python -m app.scripts.export_openapi`; do not edit
by hand. The machine-readable spec is [`openapi.json`](openapi.json); a running backend
also serves it at `/api/v1/openapi.json` with interactive docs at `/api/v1/docs`.

Every error uses one contract: `{"error": {"code", "message", "details",
"request_id"}}`. Stack traces and submitted input values are never returned. Every
response carries an `X-Request-ID` header.

| Method | Path | Summary | Success | Documented errors | Admin |
|---|---|---|---|---|---|
| GET | `/api/v1/health` | Liveness check | 200 | - |  |
| GET | `/api/v1/version` | Build and API version | 200 | - |  |
| GET | `/api/v1/ready` | Readiness of the API and its dependencies | 200 | 503 |  |
| POST | `/api/v1/auth/login` | Sign in with email and password (sets an HttpOnly session cookie) | 200 | 401, 413, 422, 429 |  |
| POST | `/api/v1/auth/logout` | Sign out this browser (the session is deleted server-side) | 204 | - |  |
| GET | `/api/v1/auth/me` | The signed-in user and their analysis stats | 200 | 401 |  |
| POST | `/api/v1/auth/password` | Change your password (signs out your other browsers) | 204 | 400, 401, 413, 422 |  |
| GET | `/api/v1/users` | List users | 200 | 401, 403, 422 |  |
| POST | `/api/v1/users` | Create a user with an initial password | 201 | 400, 401, 403, 409, 413, 422 |  |
| PATCH | `/api/v1/users/{user_id}` | Rename, change role, (de)activate, or reset a user's password | 200 | 400, 401, 403, 404, 413, 422 |  |
| POST | `/api/v1/search` | Search the knowledge base | 200 | 413, 422, 429, 502, 503 |  |
| POST | `/api/v1/tickets/retrieve` | Retrieve evidence for a support ticket | 200 | 413, 422, 429, 502, 503 |  |
| GET | `/api/v1/documents/{document_id}/chunks` | List a document's chunks (active version unless `version` is given) | 200 | 404, 422 |  |
| POST | `/api/v1/tickets/analyze` | Draft a grounded, cited resolution for a ticket and save it to your history | 200 | 401, 413, 422, 429 |  |
| POST | `/api/v1/tickets/classify` | Classify a ticket and estimate its priority (AI-assisted; not saved) | 200 | 401, 413, 422, 429 |  |
| GET | `/api/v1/tickets/history` | Your saved analyses, newest first (admins: scope=all for every user) | 200 | 401, 403, 422 |  |
| GET | `/api/v1/tickets/history/{analysis_id}` | One saved analysis: the ticket as submitted and the full result | 200 | 401, 404, 422 |  |
| DELETE | `/api/v1/tickets/history/{analysis_id}` | Delete a saved analysis (yours, or any for admins) | 204 | 401, 404, 422 |  |
| GET | `/api/v1/documents` | List knowledge-base documents | 200 | 422 |  |
| GET | `/api/v1/documents/{document_id}` | A document's current state and version history | 200 | 404, 422 |  |
| POST | `/api/v1/ingestion/run` | Start an ingestion job in the background (admin only) | 202 | 401, 403, 409, 413, 422, 502, 503 | yes |
| GET | `/api/v1/ingestion/status` | Latest ingestion job | 200 | 401 |  |
| GET | `/api/v1/ingestion/{job_id}` | One ingestion job | 200 | 401, 404, 422 |  |
| POST | `/api/v1/evaluations/run` | Start an evaluation run in the background (admin only) | 202 | 400, 401, 403, 409, 413, 422, 502 | yes |
| GET | `/api/v1/evaluations` | Recent evaluation runs | 200 | 401, 422 |  |
| GET | `/api/v1/evaluations/{evaluation_id}` | One evaluation run with per-case results | 200 | 401, 404, 422 |  |

## Error codes

| Status | Meaning |
|---|---|
| 400 | The request is invalid (`INVALID_REQUEST`). |
| 401 | Missing admin token (`UNAUTHORIZED`). |
| 403 | Invalid admin token or admin not allowed from this client (`FORBIDDEN`). |
| 404 | Resource not found (`NOT_FOUND`, `DOCUMENT_NOT_FOUND`). |
| 409 | A job of this kind is already running (`*_ALREADY_RUNNING`). |
| 413 | Request body exceeds `MAX_REQUEST_BODY_BYTES` (`PAYLOAD_TOO_LARGE`). |
| 422 | Request validation failed (`INVALID_REQUEST`); input values are never echoed. |
| 429 | Rate limit exceeded (`RATE_LIMITED`); see the `Retry-After` header. |
| 502 | A provider returned an error (`EMBEDDING_PROVIDER_ERROR`, `LLM_PROVIDER_ERROR`). |
| 503 | A dependency is unavailable (`RETRIEVAL_UNAVAILABLE`, `SERVICE_UNAVAILABLE`). |
| 500 | Unexpected failure (`INTERNAL_ERROR`); details are logged, not returned. |
