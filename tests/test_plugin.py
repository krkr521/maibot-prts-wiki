"""Test actual SDK declarations and the boundary between lookup and QQ replies."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import importlib
import logging
import re
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "prts_plugin_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
module = importlib.import_module(PACKAGE + ".plugin")


def plugin():
    instance = module.create_plugin()
    instance.set_plugin_config(instance.get_default_config())
    instance._set_context(SimpleNamespace(
        logger=logging.getLogger("prts_tests"),
        send=SimpleNamespace(text=AsyncMock(return_value=True), forward=AsyncMock(return_value=True)),
    ))
    return instance


def test_model_can_directly_call_all_three_tools_without_stream_parameter():
    components = {item["name"]: item for item in plugin().get_components()}
    for name in ("prts_search", "prts_query", "prts_recruit"):
        metadata = components[name]["metadata"]
        assert metadata["visibility"] == "visible"
        assert metadata["invoke_method"] == "plugin.invoke_tool"
        assert "stream_id" not in metadata["parameters_raw"]["properties"]
        assert metadata["parameters_raw"]["additionalProperties"] is False


def test_query_returns_complete_section_and_source_without_sending_messages():
    async def run():
        instance = plugin()
        await instance.on_load()
        instance._client = SimpleNamespace(query=AsyncMock(return_value={
            "success": True, "title": "能天使", "content": "技能专精3：攻击变为5连射", "url": "https://prts.wiki/w/能天使",
            "images": [{"url": "https://media.prts.wiki/test.png"}], "sections": ["技能", "天赋"],
        }))
        result = await instance.prts_query(query="能天使", category="operator", section="技能", stream_id="host_context")
        assert result["success"] is True
        assert "5连射" in result["content"] and "https://prts.wiki/w/能天使" in result["content"]
        assert result["content"].count("PRTS：能天使") == 1
        assert result["images"][0]["url"] == "https://media.prts.wiki/test.png"
        instance.ctx.send.text.assert_not_called()
        instance.ctx.send.forward.assert_not_called()
        instance._client.query.assert_awaited_once_with(query="能天使", category="operator", section="技能", max_chars=10000)
    asyncio.run(run())


@pytest.mark.parametrize("arguments", [
    {"query": "https://example.com"}, {"query": "C:\\private\\file"}, {"query": "x\nsecret"},
    {"query": "x" * 129}, {"query": ""}, {"query": "能天使", "limit": True},
])
def test_search_invalid_arguments_never_reach_network(arguments):
    async def run():
        instance = plugin()
        await instance.on_load()
        result = await instance.prts_search(**arguments)
        assert result["success"] is False
        assert result["error"] == "invalid_arguments"
        assert instance._client is None
    asyncio.run(run())


def test_unmentioned_group_command_is_silent_and_has_no_network_effect():
    async def run():
        instance = plugin()
        instance.prts_query = AsyncMock()
        result = await instance.handle_command(
            stream_id="test", group_id="group", matched_groups={"action": "prts", "query": "能天使"},
            message={"message_info": {"group_info": {"group_id": "group"}}},
        )
        assert result[0] is False
        instance.prts_query.assert_not_called()
        instance.ctx.send.text.assert_not_called()
    asyncio.run(run())


def test_mentioned_command_strips_at_and_sends_one_response():
    async def run():
        instance = plugin()
        instance.prts_query = AsyncMock(return_value={"success": True, "content": "查询正文与来源"})
        result = await instance.handle_command(
            stream_id="test", group_id="group", message={
                "is_at": True, "message_info": {"group_info": {"group_id": "group"}},
                "raw_message": [{"type": "text", "data": "/prts 能天使#技能"}, {"type": "at", "data": "bot"}],
            },
        )
        assert result[0] is True
        instance.prts_query.assert_awaited_once_with("能天使", "auto", "技能")
        instance.ctx.send.text.assert_awaited_once_with("查询正文与来源", "test")
    asyncio.run(run())


def test_config_reload_and_unload_close_http_client_and_disable_queries():
    async def run():
        instance = plugin()
        await instance.on_load()
        client = SimpleNamespace(close=AsyncMock())
        instance._client = client
        await instance.on_config_update("self", {}, "1.0.0")
        client.close.assert_awaited_once()
        assert instance._client is None
        await instance.on_unload()
        result = await instance.prts_query("能天使")
        assert result["error"] == "inactive"
    asyncio.run(run())


def test_source_delivery_includes_distributable_files_but_excludes_runtime_secrets(tmp_path, monkeypatch):
    (tmp_path / "plugin.py").write_text("# distributable code", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("AGPL", encoding="utf-8")
    (tmp_path / "config.toml").write_text('secret="DO_NOT_DELIVER"', encoding="utf-8")
    (tmp_path / "local.key").write_text("DO_NOT_DELIVER", encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(tmp_path / "plugin.py"))
    result = module._source_text()
    assert "distributable code" in result and "AGPL" in result
    assert "DO_NOT_DELIVER" not in result


def test_command_requires_slash_and_does_not_match_substrings():
    assert re.fullmatch(module.COMMAND_PATTERN, "/prts 能天使")
    assert re.fullmatch(module.COMMAND_PATTERN, "/akngy 能天使，时装1")
    assert not re.fullmatch(module.COMMAND_PATTERN, "prts 能天使")
    assert not re.fullmatch(module.COMMAND_PATTERN, "聊一下 /prts 能天使")


def test_recruit_formatter_does_not_present_five_tags_as_a_selectable_set():
    text = module._result_text({
        "success": True, "tags": ["治疗", "近战位", "支援", "先锋", "输出"], "full_match": [],
        "combinations": [{"tags": ["治疗", "近战位"], "min_rarity": 4, "total": 1,
                          "operators": [{"name": "古米", "rarity": 4}]}],
        "note": "最多选择3个标签", "url": "https://prts.wiki/w/公开招募",
    })
    assert "全部所选标签匹配" not in text
    assert "古米（4星）" in text and "最多选择3个标签" in text
