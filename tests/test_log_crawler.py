"""LogCrawler scaffolds for MON-01/MON-02 (full behavior in 02-02)."""
import pytest

from log_crawler import LogCrawler


@pytest.mark.xfail(reason="MON Wave2: LogCrawler critical extract not implemented until 02-02", strict=False)
@pytest.mark.asyncio
async def test_critical_event_extract(sample_docker_log_lines):
    crawler = LogCrawler()
    events = await crawler.get_critical_events(lines=100)
    # Expect ERROR/FATAL/OOM-style structured dicts when implemented
    assert isinstance(events, list)
    assert len(events) > 0
    assert any(
        e.get("level") in ("ERROR", "FATAL", "OOM") or "OOM" in str(e.get("message", "")).upper()
        for e in events
    )


@pytest.mark.xfail(reason="MON Wave2: no_raw_dump guard not implemented until 02-02", strict=False)
@pytest.mark.asyncio
async def test_no_raw_dump(sample_docker_log_lines):
    crawler = LogCrawler()
    events = await crawler.get_critical_events(lines=100)
    summary = await crawler.summarize(events or [{"message": line} for line in sample_docker_log_lines])
    # Summarize must not embed multi-KB raw log dumps
    assert len(summary) < 2000
    assert sample_docker_log_lines[0] not in summary or summary.count("\n") < 20


@pytest.mark.xfail(reason="MON Wave2: llm_polish fallback path lands in 02-02", strict=False)
@pytest.mark.asyncio
async def test_llm_fallback():
    crawler = LogCrawler()
    events = [
        {"timestamp": "t", "level": "ERROR", "message": "boom", "container": "litellm"},
    ]
    summary = await crawler.summarize(events)
    assert summary
    assert "ERROR" in summary or "crítico" in summary.lower() or "critical" in summary.lower() or "•" in summary
