"""The support category taxonomy, shared by the manifest, classification, and evaluation."""

from enum import StrEnum


class Category(StrEnum):
    AUTHENTICATION = "authentication"
    API = "api"
    CI_CD = "ci_cd"
    RUNNERS = "runners"
    PROJECTS = "projects"
    PERMISSIONS = "permissions"
    INTEGRATIONS = "integrations"
    WEBHOOKS = "webhooks"
    TROUBLESHOOTING = "troubleshooting"
    SECURITY = "security"
    CONFIGURATION = "configuration"
