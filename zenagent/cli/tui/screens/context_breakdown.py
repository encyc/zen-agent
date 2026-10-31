"""Read-only context breakdown: exact characters, estimated token slices."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from zenagent.app.context import ContextReport
from zenagent.cli.tui import palette
from zenagent.cli.tui.screens.modals import DialogScreen

__all__ = ["ContextBreakdownScreen"]


class ContextBreakdownScreen(DialogScreen[None]):
    """Every context slice: label, ≈tokens, share, exact characters."""

    BINDINGS = [("escape", "close", "Close")]  # noqa: RUF012 - Textual class API

    DEFAULT_CSS = """
    ContextBreakdownScreen #context-body {
        height: auto;
        max-height: 60vh;
    }
    ContextBreakdownScreen .context-row {
        height: 1;
    }
    ContextBreakdownScreen .context-note {
        height: auto;
        color: #5d6f7e;
    }
    """

    def __init__(self, report: ContextReport) -> None:
        super().__init__()
        self.report = report

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("context"), id="dialog-title")
            with Vertical(id="context-body"):
                yield Static(self._total_row(), classes="context-row")
                for item in self.report.slices:
                    yield Static(self._slice_row(item), classes="context-row")
                if self.report.window:
                    yield Static(self._free_row(), classes="context-row")
            yield Static(Text(self._note()), classes="context-note")
            yield Static(Text("[esc] close"), id="dialog-hint")

    def _total_row(self) -> Text:
        report = self.report
        pct = report.total_tokens / report.window * 100 if report.window else 0.0
        mark = "" if report.measured else "≈"
        text = Text()
        text.append("total ", style=palette.TEXT)
        text.append(
            f"{mark}{_human(report.total_tokens)}/{_human(report.window)}",
            style=palette.TEXT,
        )
        text.append(f"  {pct:.0f}%", style=palette.ACCENT)
        return text

    def _slice_row(self, item) -> Text:
        text = Text()
        text.append(
            f"{item.label:<12}", style=palette.STATE_COLORS.get(item.id, palette.MUTED)
        )
        text.append(f" ≈{item.tokens:<6}", style=palette.MUTED)
        text.append(f" {item.pct:>5.1f}%", style=palette.TEXT)
        chars = f"{item.chars:>6} ch" if item.chars else "      —"
        text.append(f"  {chars}", style=palette.DIM)
        return text

    def _free_row(self) -> Text:
        pct = self.report.free_tokens / self.report.window * 100
        text = Text()
        text.append(f"{'free space':<12}", style=palette.DIM)
        text.append(f" ≈{self.report.free_tokens:<6}", style=palette.DIM)
        text.append(f" {pct:>5.1f}%", style=palette.DIM)
        return text

    def _note(self) -> str:
        base = "percentages are shares of the model window"
        if self.report.measured:
            return f"{base}; slices are estimates (≈), the used total is provider-measured"
        return f"{base}; slices and total are estimated (≈) — no provider usage yet"

    def action_close(self) -> None:
        self.dismiss(None)


def _human(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}m"
    if count >= 1_000:
        return f"{count / 1_000:.0f}k"
    return str(count)
