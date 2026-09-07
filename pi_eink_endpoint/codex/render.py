"""Codex-branded quota screen renderer."""

from pi_eink_endpoint.quota.render import QuotaScreenRenderer


renderer = QuotaScreenRenderer(title="CODEX", icon_name="codex-icon.xbm")


def render_quota(quota, timezone_name, *, error=None):
    return renderer.render_quota(quota, timezone_name, error=error)


def render_login(verification_url, user_code, *, error=None):
    return renderer.render_login(verification_url, user_code, error=error)
