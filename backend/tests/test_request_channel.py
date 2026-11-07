"""The client a citizen request came through is kept for the request being served."""
import asyncio

from app.auth.channel import request_client, set_request_client


def test_each_request_sees_its_own_client():
    async def request(client_id):
        set_request_client(client_id)
        await asyncio.sleep(0)
        return request_client()

    async def both():
        return await asyncio.gather(asyncio.create_task(request("mcp-cerebrito-demo")),
                                    asyncio.create_task(request("citizen-portal-demo")))
    assert asyncio.run(both()) == ["mcp-cerebrito-demo", "citizen-portal-demo"]
    assert request_client() is None
