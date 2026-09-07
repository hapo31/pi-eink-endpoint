"""HTTP boundary for the Codex display service."""

from pi_eink_endpoint.quota.router import create_router


router = create_router("codex")
