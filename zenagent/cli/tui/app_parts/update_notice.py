"""Update check: cached notice at startup, one background refresh, never fatal."""

from __future__ import annotations

from zenagent import update_check
from zenagent.cli.tui.widgets.transcript import TranscriptView


class UpdateNoticeMixin:
    """Startup update check. State (``_update_notice_shown``) stays on the App."""

    def _start_update_check(self) -> None:
        """Kick off the cached notice and background refresh; never break startup."""
        try:
            if update_check.is_disabled() or not update_check.installed_version():
                return
            self._maybe_show_cached_update()
            self.run_worker(
                self._refresh_update_check(), exclusive=False, exit_on_error=False
            )
        except Exception:  # noqa: BLE001 - a check must never break the UI
            return

    def _maybe_show_cached_update(self) -> None:
        try:
            version = update_check.pending_notice()
        except Exception:  # noqa: BLE001 - a check must never break the UI
            return
        if version:
            self._show_update_notice(version)

    def _show_update_notice(self, version: str) -> None:
        try:
            if self._update_notice_shown:
                return
            installed = update_check.installed_version()
            if not installed:
                return
            notice = update_check.format_notice(version, installed)
            # Parent transcript only: the subagent view is a read-only swap.
            self.query_one("#transcript", TranscriptView).append_notice(notice)
            update_check.mark_notified(version)
            self._update_notice_shown = True
        except Exception:  # noqa: BLE001 - a check must never break the UI
            return

    async def _refresh_update_check(self) -> None:
        try:
            version = await update_check.refresh()
        except Exception:  # noqa: BLE001 - a check must never break the UI
            return
        if version:
            self._show_update_notice(version)
