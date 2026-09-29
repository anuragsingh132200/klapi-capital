import httpx
import pytest

from app.brokers.live import AngelOneAdapter, UpstoxAdapter
from app.domain import PlannedOrder, Side


@pytest.mark.asyncio
async def test_angel_one_resolves_symbol_token_from_instrument_master():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "OpenAPIScripMaster.json" in str(request.url)
        return httpx.Response(
            200,
            json=[
                {
                    "token": "2885",
                    "symbol": "RELIANCE-EQ",
                    "exch_seg": "NSE",
                }
            ],
        )

    adapter = AngelOneAdapter(
        {"api_key": "key", "access_token": "token"}
    )
    await adapter._client.aclose()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    order = PlannedOrder(symbol="RELIANCE", exchange="NSE", side=Side.BUY, quantity=1)

    assert await adapter._symbol_token(order) == "2885"
    # The second lookup is served from the adapter cache.
    assert await adapter._symbol_token(order) == "2885"
    await adapter.close()


@pytest.mark.asyncio
async def test_upstox_resolves_exact_equity_instrument_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/instruments/search"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "trading_symbol": "RELIANCE",
                        "exchange": "NSE",
                        "instrument_type": "EQ",
                        "instrument_key": "NSE_EQ|INE002A01018",
                    }
                ]
            },
        )

    adapter = UpstoxAdapter({"access_token": "token"})
    await adapter._client.aclose()
    adapter._client = httpx.AsyncClient(
        base_url="https://api.upstox.com", transport=httpx.MockTransport(handler)
    )
    order = PlannedOrder(symbol="RELIANCE", exchange="NSE", side=Side.BUY, quantity=1)

    assert await adapter._instrument_key(order) == "NSE_EQ|INE002A01018"
    await adapter.close()
