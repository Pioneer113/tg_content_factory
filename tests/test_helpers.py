import asyncio

from tests.helpers import fake_asyncio_run


def test_fake_asyncio_run_closes_async_generators(monkeypatch):
    closed = []

    async def stream():
        try:
            yield "chunk"
        finally:
            await asyncio.sleep(0)
            closed.append(True)

    async def run():
        iterator = stream()
        assert await anext(iterator) == "chunk"
        return iterator

    monkeypatch.setattr(asyncio, "run", fake_asyncio_run)
    iterator = asyncio.run(run())

    assert iterator.ag_frame is None
    assert closed == [True]
