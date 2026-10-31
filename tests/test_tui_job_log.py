"""Background job log: click a sidebar job to read its bounded output."""

from zenagent.cli.tui.app import ZenAgentApp
from zenagent.cli.tui.screens.job_log import JobLogScreen
from zenagent.cli.tui.widgets.prompt_input import PromptInput
from zenagent.cli.tui.widgets.sidebar import SidebarRow
from zenagent.cli.tui.widgets.transcript import TranscriptView


class FakeJob:
    def __init__(
        self,
        job_id: str = "bg_1",
        *,
        command: str = "pytest -q",
        status: str = "running",
        output: str = "collecting\nrunning tests\n",
        truncated: bool = False,
        duration_ms: int | None = None,
        exit_code: int | None = None,
    ) -> None:
        self.id = job_id
        self.command = command
        self.status = status
        self._output = output
        self.truncated = truncated
        self.duration_ms = duration_ms
        self.exit_code = exit_code
        self.started_at = 0.0

    @property
    def output(self) -> str:
        return self._output


class FakeManager:
    def __init__(self, *jobs: FakeJob) -> None:
        self.jobs = list(jobs)

    def list(self) -> list:
        return list(self.jobs)

    def snapshot(self, job_id: str):
        return next((job for job in self.jobs if job.id == job_id), None)


async def _show_jobs_row(app, pilot, key: str = "bg_1") -> None:
    await pilot.press("ctrl+b")
    await pilot.pause()
    row = next(row for row in app.query(SidebarRow) if row.key == key)
    await pilot.click(row)
    await pilot.pause()


async def test_clicking_a_job_row_opens_its_log(fake_runtime, monkeypatch):
    manager = FakeManager(FakeJob())
    monkeypatch.setattr("zenagent.app.chat.get_job_manager", lambda: manager)

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app._refresh_status()
        await pilot.pause()
        await _show_jobs_row(app, pilot)

        assert isinstance(app.screen, JobLogScreen)
        text = " ".join(
            str(widget.content) for widget in app.screen.query(".job-log-text")
        )
        assert "bg_1" in text
        assert "pytest -q" in text
        assert "collecting" in text


async def test_a_running_job_log_refreshes_with_the_poll(fake_runtime, monkeypatch):
    job = FakeJob()
    manager = FakeManager(job)
    monkeypatch.setattr("zenagent.app.chat.get_job_manager", lambda: manager)

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app._refresh_status()
        await pilot.pause()
        await _show_jobs_row(app, pilot)
        screen = app.screen
        assert isinstance(screen, JobLogScreen)

        job._output += "test_sidebar.py ....\n"
        app._refresh_status()
        await pilot.pause()

        text = " ".join(
            str(widget.content) for widget in screen.query(".job-log-text")
        )
        assert "test_sidebar.py" in text


async def test_a_truncated_log_says_so(fake_runtime, monkeypatch):
    manager = FakeManager(FakeJob(truncated=True, status="ok", duration_ms=1200, exit_code=0))
    monkeypatch.setattr("zenagent.app.chat.get_job_manager", lambda: manager)

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app._refresh_status()
        await pilot.pause()
        await _show_jobs_row(app, pilot)

        text = " ".join(
            str(widget.content) for widget in app.screen.query(".job-log-text")
        )
        assert "truncated" in text.lower()


async def test_esc_closes_the_log_and_restores_the_prompt(fake_runtime, monkeypatch):
    manager = FakeManager(FakeJob())
    monkeypatch.setattr("zenagent.app.chat.get_job_manager", lambda: manager)

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app._refresh_status()
        await pilot.pause()
        await _show_jobs_row(app, pilot)

        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, JobLogScreen)
        assert app.focused is app.query_one(PromptInput)


async def test_a_retired_job_shows_a_notice_instead_of_a_screen(fake_runtime, monkeypatch):
    manager = FakeManager()  # snapshot() returns None: the job was already waited
    monkeypatch.setattr("zenagent.app.chat.get_job_manager", lambda: manager)

    app = ZenAgentApp(fake_runtime)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.show_job_log("bg_gone")
        await pilot.pause()

        assert not isinstance(app.screen, JobLogScreen)
        assert app.query_one(TranscriptView).display is True
