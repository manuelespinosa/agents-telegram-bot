"""ReportScheduler JobQueue registration (daily_health_report)."""
from types import SimpleNamespace

from scheduler import ReportScheduler


def test_setup_registers_daily_health_report_job():
    registered = []

    class FakeJobQueue:
        def run_daily(self, callback, time, name=None, **kwargs):
            registered.append(
                {"callback": callback, "time": time, "name": name, "kwargs": kwargs}
            )

    app = SimpleNamespace(job_queue=FakeJobQueue(), bot_data={})
    scheduler = ReportScheduler()
    scheduler.setup(app)

    names = [j.get("name") for j in registered]
    assert "daily_health_report" in names
    job = next(j for j in registered if j["name"] == "daily_health_report")
    assert job["time"] is not None
    assert getattr(job["time"], "hour", None) == 8
    assert job["callback"] is not None


def test_setup_raises_when_job_queue_missing():
    app = SimpleNamespace(job_queue=None, bot_data={})
    scheduler = ReportScheduler()
    try:
        scheduler.setup(app)
        raised = False
    except RuntimeError as e:
        raised = True
        assert "job-queue" in str(e).lower() or "jobqueue" in str(e).lower()
    assert raised


def test_no_asyncio_scheduler_import():
    import inspect
    import scheduler as sched_mod

    src = inspect.getsource(sched_mod)
    assert "AsyncIOScheduler" not in src
    assert "apscheduler" not in src.lower()
