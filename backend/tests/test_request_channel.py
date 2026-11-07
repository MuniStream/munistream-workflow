"""The client a citizen request came through is kept for the request being served."""
import asyncio

from app.auth.channel import channel_of, request_client, set_request_client


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


def test_the_agent_behind_the_mcp_is_the_channel_only_for_mcp_tokens():
    assert channel_of("munistream-mcp-conapesca", "mcp-cerebrito-conapesca") == "mcp-cerebrito-conapesca"
    assert channel_of("munistream-mcp-conapesca", None) == "munistream-mcp-conapesca"
    assert channel_of("conapesca-citizen-public", "mcp-cerebrito-conapesca") == "conapesca-citizen-public"   # ignored
    assert channel_of(None, None) is None
    from fastapi import Header
    assert channel_of("munistream-mcp-conapesca", Header(None)) == "munistream-mcp-conapesca"   # called directly
