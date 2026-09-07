"""Codex-branded wrappers around shared quota screen rendering."""

from pi_eink_endpoint.quota.render import render_login as _render_login
from pi_eink_endpoint.quota.render import render_quota as _render_quota


def render_quota(quota, timezone_name, *, error=None):
    return _render_quota(quota, timezone_name, title="CODEX", icon_name="codex-icon.xbm", error=error)


def render_login(verification_url, user_code, *, error=None):
    return _render_login(verification_url, user_code, title="CODEX", icon_name="codex-icon.xbm", error=error)
