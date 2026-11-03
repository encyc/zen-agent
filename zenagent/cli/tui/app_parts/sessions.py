"""Session-switch follow-through: applying a picker action, resetting the UI."""

from __future__ import annotations

from zenagent.cli.tui.widgets.transcript import TranscriptView
from zenagent.i18n import t


class SessionsMixin:
    """Session actions. State (``_session_scope``, ``_viewing``) stays on the App."""

    async def _apply_session_action(self, action) -> None:
        from zenagent.agent.session import session_title

        if action.kind == "resume" and action.path is not None:
            from zenagent import project

            owner = project.project_key_for_session(action.path)
            current = project.project_key(project.find_project_root())
            await self.sessions.resume(action.path)
            self._reset_after_switch()
            if owner is not None and owner != current:
                self.notify(t("tui.app.cross_project"))
            elif owner is None:
                self.notify(t("tui.app.legacy_session"))
            return
        if action.kind in ("new", "fork"):
            if action.kind == "new":
                await self.sessions.new()
            else:
                await self.sessions.fork()
            self._reset_after_switch()
            return
        target = action.path or self.sessions.current()
        if action.kind == "delete":
            prompt = t("tui.app.delete_confirm", name=target.name)
            if not await self._confirm(prompt):
                return
            was_current = target == self.sessions.current()
            self.sessions.delete(target)
            if was_current:
                self._reset_after_switch()
            self._refresh_status()
            self.notify(t("tui.app.session_trashed"))
        elif action.kind == "export":
            exported = self.sessions.export(target)
            self.notify(t("tui.app.exported", path=exported))
        elif action.kind == "rename":
            name = await self._prompt_rename(session_title(target))
            if name is None:
                return
            try:
                stored = self.sessions.rename(target, name)
            except ValueError as exc:
                self.notify(str(exc))
                return
            self.notify(t("tui.app.renamed", name=stored))

    def _reset_after_switch(self) -> None:
        if self._viewing is not None:
            self._exit_subagent_view(restore_focus=False)
        for log in self.query(TranscriptView):
            log.reset()
        self.chat.reload_transcript(drop_pending=True)
        self._refresh_status()
