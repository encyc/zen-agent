"""Theme and interface-language switching, including post-switch repaint."""

from __future__ import annotations

from zenagent import settings
from zenagent.app.appearance import THEME_CHOICES, resolve_theme
from zenagent.cli.tui.app_theme import theme_name
from zenagent.cli.tui.widgets.sidebar import Sidebar
from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.i18n import LANGUAGE_CHOICES, resolve_language, set_locale


class AppearanceMixin:
    """Theme/language switching. State (``_theme_spec`` etc.) stays on the App."""

    def set_appearance(self, spec: str) -> str:
        """Switch the palette to ``spec`` (auto|dark|light); persist known values."""

        normalized = (spec or "").strip().lower()
        self._apply_appearance(resolve_theme(normalized))
        if normalized in THEME_CHOICES:
            settings.set_pref("theme", normalized)
            self._theme_spec = normalized
        return theme_name(self._appearance)

    def _apply_appearance(self, appearance) -> None:
        from zenagent.cli.tui import palette

        palette.bind(appearance)
        self._appearance = appearance
        self.theme = theme_name(appearance)
        self._repaint_rich()

    def _repaint_rich(self) -> None:
        """Rebuild the text that carries literal colours (not CSS variables).

        A theme switch repaints stylesheet-driven widgets on its own, but the
        transcript rows and status band embed Rich styles from ``palette``, so
        they are rebuilt from the current views instead.
        """
        for log in self.query(TranscriptView):
            log.reset()
        self._refresh_status()

    def set_language(self, spec: str) -> str:
        """Switch the interface language (auto|en|zh) and rebuild the chrome."""
        normalized = (spec or "").strip().lower()
        code = resolve_language(normalized)
        set_locale(code)
        if normalized in LANGUAGE_CHOICES:
            settings.set_pref("language", normalized)
            self._language_spec = normalized
        self._rebuild_commands()
        for sidebar in self.query(Sidebar):
            sidebar.refresh_language()
        self._repaint_rich()
        return code

    def _announce_language_warnings(self) -> None:
        """Report a rejected language pref without stopping the app."""
        warnings, self._language_warnings = self._language_warnings, []
        if not warnings:
            return
        transcript = self.query_one(TranscriptView)
        for warning in warnings:
            transcript.append_notice(f"language: {warning}", warning=True)

    def _announce_appearance_warnings(self) -> None:
        """Report a rejected theme pref without stopping the app."""
        warnings, self._appearance_warnings = self._appearance_warnings, []
        if not warnings:
            return
        transcript = self.query_one(TranscriptView)
        for warning in warnings:
            transcript.append_notice(f"theme: {warning}", warning=True)
