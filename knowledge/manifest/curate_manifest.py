"""Build knowledge/manifest/sources.json from the GitLab docs English sitemap.

Reproducible curation: every URL comes from the published sitemap (so it exists),
is assigned to one of the 11 support categories by ordered path rules, and each
category is capped. Within a category, general pages (shallower paths) come first.

Usage (from the repository root):
    curl -s https://docs.gitlab.com/en-us/sitemap.xml -o sitemap.xml
    python knowledge/manifest/curate_manifest.py sitemap.xml

Titles are filled from each page's <h1> during ingestion; the manifest title is a
readable placeholder derived from the URL path and is replaced on first fetch.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LICENSE = "CC BY-SA 4.0"
SOURCE_TYPE = "product_documentation"
BASE = "https://docs.gitlab.com/"

# Ordered: first matching rule wins. (category, cap, path regex)
RULES: list[tuple[str, int, str]] = [
    ("troubleshooting", 25, r"troubleshoot"),
    ("webhooks", 14, r"(^|/)(webhooks?|webhook_events|system_hooks|file_hooks)(/|$)"),
    (
        "authentication",
        22,
        r"^(user/profile/(personal_access_tokens|account/two_factor_authentication|service_accounts)"
        r"|security/(tokens|two_factor_authentication|password_length_limits)"
        r"|api/(rest/authentication|oauth2)"
        r"|user/project/(deploy_tokens|deploy_keys|settings/project_access_tokens)"
        r"|user/group/(saml_sso|settings/group_access_tokens)"
        r"|administration/auth"
        r"|user/ssh"
        r"|ci/jobs/ci_job_token"
        r"|integration/(oauth_provider|saml|omniauth|ldap))",
    ),
    ("runners", 22, r"^(runner|ci/runners)(/|$)"),
    ("ci_cd", 25, r"^ci(/|$)"),
    ("api", 22, r"^api/(?!graphql/reference)"),
    (
        "permissions",
        14,
        r"^(user/permissions|user/custom_roles|user/project/members|user/group/manage"
        r"|user/project/repository/branches/protected|user/project/protected_tags"
        r"|user/project/merge_requests/approvals"
        r"|administration/settings/visibility_and_access_controls)",
    ),
    ("security", 20, r"^(user/application_security|security)(/|$)"),
    ("integrations", 18, r"^(user/project/integrations|integration)(/|$)"),
    ("projects", 22, r"^user/project(/|$)"),
    ("configuration", 22, r"^(administration|install|omnibus/settings)(/|$)"),
]

# Pages support tickets most often need, per category, always included first (in this order).
# Every path must exist in the sitemap; missing ones are reported, never invented.
PRIORITY: dict[str, list[str]] = {
    "troubleshooting": [
        "ci/variables/variables_troubleshooting/", "ci/jobs/job_artifacts_troubleshooting/",
        "ci/docker/docker_build_troubleshooting/", "api/rest/troubleshooting/",
        "user/project/integrations/webhooks_troubleshooting/",
        "user/project/repository/mirror/troubleshooting/",
        "user/profile/account/two_factor_authentication_troubleshooting/",
        "administration/auth/ldap/ldap-troubleshooting/",
        "user/project/integrations/gitlab_slack_app_troubleshooting/",
        "administration/backup_restore/troubleshooting_backup_gitlab/",
        "administration/packages/container_registry_troubleshooting/",
        "administration/cicd/job_artifacts_troubleshooting/",
    ],
    "webhooks": [
        "user/project/integrations/webhooks/", "user/project/integrations/webhook_events/",
        "administration/system_hooks/", "api/system_hooks/", "security/webhooks/",
    ],
    "authentication": [
        "api/rest/authentication/", "user/profile/personal_access_tokens/",
        "user/profile/account/two_factor_authentication/", "security/two_factor_authentication/",
        "ci/jobs/ci_job_token/", "user/project/deploy_tokens/", "user/project/deploy_keys/",
        "api/oauth2/", "user/ssh/", "user/group/saml_sso/", "administration/auth/ldap/",
        "administration/auth/oidc/", "api/personal_access_tokens/", "security/tokens/",
    ],
    "runners": [
        "ci/runners/", "runner/", "runner/install/", "runner/register/",
        "runner/configuration/advanced-configuration/", "runner/configuration/tls-self-signed/",
        "runner/executors/", "runner/executors/docker/", "runner/executors/kubernetes/",
        "runner/executors/shell/", "runner/faq/", "runner/commands/",
        "runner/configuration/proxy/", "runner/security/", "ci/runners/configure_runners/",
        "ci/runners/hosted_runners/", "api/runners/",
    ],
    "ci_cd": [
        "ci/", "ci/yaml/", "ci/variables/", "ci/variables/predefined_variables/",
        "ci/variables/where_variables_can_be_used/", "ci/pipelines/", "ci/jobs/",
        "ci/jobs/job_control/", "ci/jobs/job_artifacts/", "ci/caching/", "ci/environments/",
        "ci/triggers/", "ci/docker/", "ci/docker/using_docker_build/", "ci/docker/docker_in_docker/",
        "ci/docker/authenticate_registry/", "ci/secrets/", "ci/debugging/",
        "ci/pipeline_security/",
    ],
    "api": [
        "api/rest/", "api/projects/", "api/merge_requests/", "api/pipelines/", "api/jobs/",
        "api/users/", "api/groups/", "api/repository_files/", "api/project_level_variables/",
        "api/pipeline_triggers/", "api/job_artifacts/", "api/rest/third_party_clients/",
        "api/protected_branches/", "api/deploy_tokens/", "api/deploy_keys/",
        "api/merge_request_approvals/",
    ],
    "permissions": [
        "user/permissions/", "user/project/members/", "user/custom_roles/",
        "user/project/repository/branches/protected/", "user/project/protected_tags/",
        "user/project/merge_requests/approvals/",
        "administration/settings/visibility_and_access_controls/",
        "user/project/members/sharing_projects_groups/", "ci/jobs/fine_grained_permissions/",
    ],
    "security": [
        "security/", "user/application_security/", "user/application_security/secret_detection/",
        "user/application_security/sast/", "user/application_security/dependency_scanning/",
        "security/rate_limits/",
    ],
    "integrations": [
        "user/project/integrations/", "integration/", "integration/jira/",
        "user/project/integrations/slack/", "user/project/integrations/gitlab_slack_application/",
    ],
    "projects": [
        "user/project/", "user/project/repository/", "user/project/merge_requests/",
        "user/project/settings/", "user/project/repository/mirror/",
        "user/project/repository/mirror/push/", "user/project/repository/mirror/pull/",
        "user/project/pages/",
    ],
    "configuration": [
        "administration/", "administration/settings/", "administration/environment_variables/",
        "administration/instance_limits/", "administration/logs/",
        "administration/packages/container_registry/", "administration/incoming_email/",
        "administration/reply_by_email/", "administration/raketasks/",
        "administration/settings/rate_limits_on_raw_endpoints/",
        "administration/settings/rate_limits_on_git_ssh_operations/",
    ],
}

# Never ingest these sections: not support-relevant, versioned archives, or huge generated references.
EXCLUDE = re.compile(
    r"^(releases|development|charts|operator|orbit|solutions|tutorials|cli|editor_extensions"
    r"|subscriptions|update|legal|[0-9])(/|$)|deprecat|removals|graphql/reference|/index\.html$"
)


def _document_id(path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", path.strip("/").lower()).strip("-")
    return slug[:200]


def _title_from_path(path: str) -> str:
    last = path.strip("/").split("/")[-1]
    return last.replace("_", " ").replace("-", " ").strip().capitalize() or "GitLab Docs"


def curate(urls: list[str]) -> tuple[list[dict[str, object]], list[str]]:
    available = {
        url[len(BASE) :]
        for url in urls
        if url.startswith(BASE) and url != BASE and not EXCLUDE.search(url[len(BASE) :])
    }
    missing = [p for paths in PRIORITY.values() for p in paths if p not in available]
    prioritized = {p for paths in PRIORITY.values() for p in paths}

    buckets: dict[str, list[str]] = {category: [] for category, _, _ in RULES}
    for path in sorted(available - prioritized):
        for category, _, pattern in RULES:
            if re.search(pattern, path):
                buckets[category].append(path)
                break

    entries: list[dict[str, object]] = []
    for category, cap, _ in RULES:
        first = [p for p in PRIORITY.get(category, []) if p in available]
        fill = sorted(buckets[category], key=lambda p: (p.count("/"), p))
        chosen = (first + fill)[: max(cap, len(first))]
        for path in chosen:
            entries.append(
                {
                    "document_id": _document_id(path),
                    "title": _title_from_path(path),
                    "canonical_url": BASE + path,
                    "category": category,
                    "source_type": SOURCE_TYPE,
                    "license": LICENSE,
                    # GitLab publishes these pages as rolling "latest" docs with no version marker.
                    "source_version": None,
                    "enabled": True,
                }
            )
    return entries, missing


def main() -> None:
    sitemap = Path(sys.argv[1]).read_text(encoding="utf-8")
    urls = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    entries, missing = curate(urls)
    if missing:
        print(f"WARNING: priority paths not in sitemap (skipped): {missing}")
    out = Path(__file__).with_name("sources.json")
    out.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8", newline="\n")
    counts: dict[str, int] = {}
    for e in entries:
        counts[str(e["category"])] = counts.get(str(e["category"]), 0) + 1
    print(f"wrote {len(entries)} entries to {out}: {counts}")


if __name__ == "__main__":
    main()
