from __future__ import annotations

import asyncio
import json

import pytest
from backend import main

def test_web_tool_loop_search_then_fetch(monkeypatch: pytest.MonkeyPatch):
    calls={"n":0}
    async def fake_gateway(payload, **kwargs):
        calls["n"]+=1
        if calls["n"]==1:
            assert payload["tool_choice"]=="required"
            return {"model":"test","choices":[{"message":{"role":"assistant","content":None,"tool_calls":[{"id":"c1","type":"function","function":{"name":"web_search","arguments":json.dumps({"query":"DEEP33"})}}]}}]}
        if calls["n"]==2:
            assert any(m.get("role")=="tool" for m in payload["messages"])
            return {"model":"test","choices":[{"message":{"role":"assistant","content":None,"tool_calls":[{"id":"c2","type":"function","function":{"name":"web_fetch","arguments":json.dumps({"url":"https://example.com/"})}}]}}]}
        assert sum(m.get("role")=="tool" for m in payload["messages"])==2
        return {"model":"test","choices":[{"message":{"role":"assistant","content":"Respuesta verificada."}}]}
    async def fake_search(query, **kwargs):
        return {"ok":True,"query":query,"results":[{"title":"Example","url":"https://example.com/","snippet":"Snippet"}]}
    async def fake_fetch(url, **kwargs):
        return {"ok":True,"url":url,"final_url":url,"title":"Example","text":"Example content"}
    monkeypatch.setattr(main,"call_gateway",fake_gateway)
    monkeypatch.setattr(main,"search_web",fake_search)
    monkeypatch.setattr(main,"fetch_page",fake_fetch)
    result,sources=asyncio.run(main.run_web_tool_loop(
        [{"role":"system","content":"web"},{"role":"user","content":"busca en internet DEEP33"}],
        model="test",request_id="r1",idempotency_key="i1",force_web=True
    ))
    assert result["choices"][0]["message"]["content"]=="Respuesta verificada."
    assert sources[0]["url"]=="https://example.com/"
    assert calls["n"]==3
