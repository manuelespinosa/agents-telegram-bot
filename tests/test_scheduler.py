"""ReportScheduler JobQueue registration scaffold (full run_daily in 02-02)."""
from types import SimpleNamespace

import pytest

from scheduler import ReportScheduler


@pytest.mark.xfail(reason="MON Wave2: JobQueue daily_health_report registration until 02-02", strict=False)
def test_setup_registers_daily_health_report_job():
    registered = []

    class FakeJobQueue:
        def run_daily(self, callback, time, name=None, **kwargs):
            registered.append({"callback": callback, "time": time, "name": name})

    app = SimpleNamespace(job_queue=FakeJobQueue(), bot_data={})
    scheduler = ReportScheduler()
    scheduler.setup(app)

    names = [j.get("name") for j in registered]
    assert "daily_health_report" in names
