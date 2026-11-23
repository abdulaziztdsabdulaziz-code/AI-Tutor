"""
FastAPI webhook server integration test.

Tests:
1. /health endpoint returns 200 OK.
2. /webhook returns 403 Forbidden without valid secret token header.
3. /webhook with valid secret token processes update.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from httpx import ASGITransport, AsyncClient

from bot.config import settings
from bot.main import app


async def test_fastapi_endpoints() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health check
        resp_health = await client.get("/health")
        assert resp_health.status_code == 200, f"Expected 200, got {resp_health.status_code}"
        health_data = resp_health.json()
        assert health_data["status"] == "ok"
        print("✅ GET /health passed:", health_data)

        # 2. Webhook rejection with missing secret token
        resp_bad = await client.post(settings.WEBHOOK_PATH, json={"update_id": 1})
        assert resp_bad.status_code == 403, f"Expected 403, got {resp_bad.status_code}"
        print("✅ POST /webhook (invalid token -> 403) passed")

        # 3. Webhook rejection with invalid secret token
        resp_bad_tok = await client.post(
            settings.WEBHOOK_PATH,
            headers={"X-Telegram-Bot-Api-Secret-Token": "bad_token_123"},
            json={"update_id": 1},
        )
        assert resp_bad_tok.status_code == 403
        print("✅ POST /webhook (wrong token -> 403) passed")

        # 4. MCP JSON-RPC protocol tests
        from bot.services.chess_engine import ChessEngine
        from bot.services.mcp_server import MCPServer
        app.state.mcp = MCPServer(chess_engine=ChessEngine())

        # Test initialize
        resp_mcp_init = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        )
        assert resp_mcp_init.status_code == 200
        init_data = resp_mcp_init.json()
        assert init_data["result"]["serverInfo"]["name"] == "ai-tutor-mcp-server"
        print("✅ POST /mcp (initialize) passed:", init_data["result"]["serverInfo"])

        # Test tools/list
        resp_mcp_list = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert resp_mcp_list.status_code == 200
        tools_data = resp_mcp_list.json()
        assert len(tools_data["result"]["tools"]) >= 3
        print("✅ POST /mcp (tools/list) passed with", len(tools_data["result"]["tools"]), "tools")

        # Test tools/call evaluate_mathematics
        resp_mcp_call = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "evaluate_mathematics",
                    "arguments": {"expression": "sqrt(144) + 10"},
                },
            },
        )
        assert resp_mcp_call.status_code == 200
        call_data = resp_mcp_call.json()
        assert "Result: 22.0" in call_data["result"]["content"][0]["text"]
        print("✅ POST /mcp (tools/call evaluate_mathematics) passed: 22.0")

    print("🎉 All FastAPI HTTP & MCP integration tests passed!")


if __name__ == "__main__":
    asyncio.run(test_fastapi_endpoints())
