"""Choose the sole quota provider allowed to update the panel."""

from __future__ import annotations

import json
from pathlib import Path


class ActiveDisplayController:
    """Coordinate Codex and Claude so their scheduled updates never compete."""

    def __init__(self, state_path: Path):
        self.state_path = Path(state_path)
        self.services = {}
        self.active_provider = None

    def register(self, provider: str, service) -> None:
        self.services[provider] = service

    async def start(self) -> None:
        """Restore at most one saved display after all providers are registered."""
        for service in self.services.values():
            await service.start(activate=False)

        enabled = [
            provider for provider, service in self.services.items()
            if service.display_enabled
        ]
        provider = self._load_active_provider()
        if provider not in enabled:
            provider = self._most_recent_enabled(enabled)
        if provider is not None:
            self.start_display(provider)

    def activate(self, provider: str):
        """Switch the panel to a provider and start its display updates."""
        self._select(provider)
        return self.services[provider].start_display()

    def start_display(self, provider: str):
        return self.activate(provider)

    def start_login(self, provider: str):
        self._select(provider)
        # Login owns its own preparation task. Start only the periodic timer
        # here to avoid beginning a second concurrent login attempt.
        self.services[provider].display.start_display(prepare=False)
        return self.services[provider].start_login()

    def refresh(self, provider: str) -> bool:
        self.activate(provider)
        return self.services[provider].refresh()

    def enqueue(self, provider: str, image, *, partial: bool = False) -> None:
        """Reject frames produced by a provider that lost the display."""
        if self.active_provider == provider:
            self._enqueue_image(image, partial=partial)

    def set_enqueue_image(self, enqueue_image) -> None:
        self._enqueue_image = enqueue_image

    def _select(self, provider: str) -> None:
        if provider not in self.services:
            raise KeyError(provider)
        for other, service in self.services.items():
            if other != provider:
                service.display.stop_display()
        self.active_provider = provider
        self._save_active_provider()

    def _most_recent_enabled(self, enabled):
        if not enabled:
            return None
        return max(
            enabled,
            key=lambda provider: self.services[provider].display.state_path.stat().st_mtime
            if self.services[provider].display.state_path.exists() else 0,
        )

    def _load_active_provider(self):
        try:
            provider = json.loads(self.state_path.read_text()).get("active_provider")
        except (OSError, ValueError, AttributeError):
            return None
        return provider if provider in self.services else None

    def _save_active_provider(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path.parent.chmod(0o700)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"active_provider": self.active_provider}))
        temporary.chmod(0o600)
        temporary.replace(self.state_path)
