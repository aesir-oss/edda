import asyncio
import threading

from fastapi.sse import ServerSentEvent

from edda.api.events import Events, stream


def _collect(events: Events, keepalive: float, publish: list[tuple[str, dict[str, object]]]) -> list[ServerSentEvent]:
    async def run() -> list[ServerSentEvent]:
        got: list[ServerSentEvent] = []
        async for item in stream(events, keepalive=keepalive):
            got.append(item)
            if item.event == "ready":
                for name, data in publish:
                    threading.Thread(target=events.publish, args=(name, data)).start()
            if len([g for g in got if g.event not in ("ready", None)]) == len(publish):
                events.close()
        return got

    return asyncio.run(asyncio.wait_for(run(), 5))


def test_streams_events_published_from_other_threads() -> None:
    events = Events()
    got = _collect(events, 5, [("proposal", {"id": "prop_000001"})])
    assert [(g.event, g.data) for g in got] == [("ready", {}), ("proposal", {"id": "prop_000001"})]


def test_sends_keepalives_and_ends_streams_when_closed() -> None:
    events = Events()

    async def run() -> list[ServerSentEvent]:
        got: list[ServerSentEvent] = []
        async for item in stream(events, keepalive=0.01):
            got.append(item)
            if item.comment == "keepalive":
                events.close()
        return got

    got = asyncio.run(asyncio.wait_for(run(), 5))
    assert [g.event for g in got] == ["ready", None]
    late = asyncio.run(asyncio.wait_for(_drain(events), 5))
    assert [g.event for g in late] == ["ready"]


async def _drain(events: Events) -> list[ServerSentEvent]:
    return [item async for item in stream(events)]


def test_publishing_with_no_subscribers_does_nothing() -> None:
    Events().publish("job", {"id": "run_0001"})
