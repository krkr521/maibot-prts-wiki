"""Behavioral tests for the PRTS client and template-preserving extraction."""

from pathlib import Path
import asyncio
import importlib
import sys
import types

import httpx


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "prts_client_tests"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package
module = importlib.import_module(PACKAGE + ".client")
parser = importlib.import_module(PACKAGE + ".parser")
PRTSClient = module.PRTSClient


def run(coro):
    return asyncio.run(coro)


def page(title, text, categories=None):
    return {"query": {"pages": [{
        "pageid": 1, "title": title,
        "revisions": [{"revid": 123, "timestamp": "2026-11-01T00:00:00Z", "slots": {"main": {"content": text}}}],
        "categories": [{"title": item} for item in (categories or [])],
    }]}}


def test_nested_templates_keep_skill_and_material_values():
    text = """==技能==
{{技能
|技能名=过载模式
|技能专精3描述=攻击变为{{color|#0098DC|5}}连射，间隔{{+|(-0.22)|{{color|#0098DC|缩短(-0.22)}}}}
|技能专精3消耗=30
|材料={{材料消耗|聚合剂|6}} {{材料消耗|糖聚块|6}}
}}"""
    clean = parser.clean_wikitext(text)
    assert "攻击变为5连射" in clean
    assert "缩短(-0.22)" in clean
    assert "技能专精3消耗：30" in clean
    assert "聚合剂 × 6" in clean
    assert "{{" not in clean


def test_section_selection_keeps_children_and_stops_at_sibling():
    text = "==技能==\n技能正文\n===技能1===\n一技能\n==后勤技能==\n后勤"
    selected, title = parser.select_section(text, "技能")
    assert title == "技能"
    assert "一技能" in selected
    assert "后勤" not in selected


def test_multiple_sections_accept_planner_space_separated_argument():
    text = "==属性==\n生命1000\n==技能==\n三技能5连射\n==天赋==\n攻击力提升\n==档案==\n其他内容"
    selected, titles, missing = parser.select_sections(text, "属性 技能 天赋")
    assert titles == ["属性", "技能", "天赋"] and missing == []
    assert "生命1000" in selected and "三技能5连射" in selected and "攻击力提升" in selected
    assert "其他内容" not in selected


def test_multiple_sections_report_missing_instead_of_claiming_complete():
    selected, titles, missing = parser.select_sections("==属性==\n生命1000\n==技能==\n技能正文", "属性、未知章节")
    assert titles == ["属性"] and missing == ["未知章节"]
    assert "技能正文" not in selected


def test_literal_heading_with_spaces_and_overlapping_parent_are_preserved():
    text = "==Level 0==\n生命1000\n==技能==\n技能\n===技能1===\n一技能\n==档案==\n档案"
    selected, titles, missing = parser.select_sections(text, "Level 0")
    assert titles == ["Level 0"] and "生命1000" in selected
    selected, titles, missing = parser.select_sections(text, "技能1、技能")
    assert titles == ["技能"] and selected.count("一技能") == 1


def test_client_multiple_sections_return_content_and_explicit_partial_warning():
    def handler(request):
        if request.url.params.get("prop") == "imageinfo":
            return httpx.Response(200, json={"query": {"pages": []}})
        return httpx.Response(200, json=page("塔露拉", "{{CharinfoV2|稀有度=5}}\n==属性==\n生命1000\n==技能==\n技能描述\n==天赋==\n天赋描述\n==档案==\n不需要的档案", ["分类:干员"]))

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = PRTSClient(http_client=http)
            result = await client.query("塔露拉", category="operator", section="属性 技能 天赋")
            assert result["success"] is True
            assert result["selected_sections"] == ["属性", "技能", "天赋"]
            assert "生命1000" in result["content"] and "天赋描述" in result["content"]
            assert "不需要的档案" not in result["content"]
            partial = await client.query("塔露拉", section="属性、未知章节")
            assert partial["success"] is True and partial["missing_sections"] == ["未知章节"]
            assert any("仅返回已匹配章节" in warning for warning in partial["warnings"])
    run(main())


def test_exact_redirect_page_and_section_return_complete_skill():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.params.get("prop") == "imageinfo":
            return httpx.Response(200, json={"query": {"pages": [{"title": "文件:立绘 能天使 2.png", "imageinfo": [{"url": "https://media.prts.wiki/image.png"}]}]}})
        return httpx.Response(200, json=page("能天使", "{{CharinfoV2\n|干员名=能天使\n|稀有度=5\n}}\n==技能==\n{{技能|技能名=过载模式|技能专精3消耗=30}}\n==档案==\n无关档案", ["分类:干员"]))

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = PRTSClient(http_client=http)
            result = await client.query("阿能", category="operator", section="技能")
            assert result["success"] is True
            assert result["title"] == "能天使"
            assert result["fields"]["稀有度"] == "6星"
            assert "技能专精3消耗：30" in result["content"]
            assert "无关档案" not in result["content"]
            assert result["images"][0]["url"] == "https://media.prts.wiki/image.png"
            assert all(request.url.host == "prts.wiki" for request in calls)
            assert calls[0].url.params["redirects"] == "1"
    run(main())


def test_search_does_not_read_unrelated_first_result_as_exact_hit():
    def handler(request):
        if request.url.params.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"title": "无关条目", "snippet": "<span>候选</span>"}]}})
        return httpx.Response(200, json={"query": {"pages": [{"title": "不存在", "missing": True}]}})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await PRTSClient(http_client=http).query("完全不存在")
            assert result["success"] is False
            assert result["error"] == "not_found"
            assert result["results"][0]["snippet"] == "候选"
    run(main())


def test_fuzzy_search_asks_model_to_choose_between_ambiguous_titles():
    def handler(request):
        if request.url.params.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [
                {"title": "能天使", "snippet": ""}, {"title": "新约能天使", "snippet": ""},
            ]}})
        return httpx.Response(200, json={"query": {"pages": [{"title": "能天", "missing": True}]}})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await PRTSClient(http_client=http).query("能天")
            assert result["error"] == "ambiguous"
            assert len(result["results"]) == 2
    run(main())


def test_empty_search_is_successful_empty_search_but_query_is_not_found():
    def handler(request):
        if request.url.params.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": []}})
        return httpx.Response(200, json={"query": {"pages": [{"title": "不存在", "missing": True}]}})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = PRTSClient(http_client=http)
            search = await client.search("不存在")
            assert search["success"] is True and search["results"] == []
            assert (await client.query("不存在"))["error"] == "not_found"
    run(main())


def test_api_errors_and_network_errors_are_not_empty_results():
    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"error": {"code": "readonly", "info": "maintenance"}}))) as http:
            result = await PRTSClient(http_client=http).search("能天使")
            assert result["success"] is False
            assert result["error"] == "api_error"

        def timeout(request):
            raise httpx.ReadTimeout("timed out", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as http:
            result = await PRTSClient(http_client=http).query("能天使")
            assert result["success"] is False
            assert result["error"] == "timeout"
    run(main())


def test_url_and_multiple_title_inputs_never_make_a_request():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = PRTSClient(http_client=http)
            for query in ("http://127.0.0.1:16099", "能天使|阿米娅", "能天使\n阿米娅"):
                assert (await client.query(query))["error"] == "invalid_argument"
            assert calls == []
    run(main())


def test_cache_is_bounded_and_result_copies_are_isolated():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"query": {"search": [{"title": "能天使", "snippet": "狙击"}]}})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = PRTSClient(http_client=http)
            first = await client.search("能天使")
            first["results"][0]["title"] = "被修改"
            second = await client.search("能天使")
            assert second["results"][0]["title"] == "能天使"
            assert len(calls) == 1
    run(main())


def test_recruit_matches_profession_position_and_senior_rules():
    operators = [
        {"cn": "能天使", "profession": "狙击", "position": "远程位", "rarity": "5", "tag": "输出"},
        {"cn": "蓝毒", "profession": "狙击", "position": "远程位", "rarity": "4", "tag": "输出"},
        {"cn": "克洛丝", "profession": "狙击", "position": "远程位", "rarity": "2", "tag": "输出"},
    ]
    def handler(request):
        assert request.url.params["where"] == "char_obtain.obtainMethod LIKE '%公开招募%'"
        return httpx.Response(200, json={"cargoquery": [{"title": op} for op in operators]})

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = PRTSClient(http_client=http)
            normal = await client.recruit("狙击 远程")
            assert normal["success"] is True
            assert {op["name"] for op in normal["full_match"]} == {"蓝毒", "克洛丝"}
            senior = await client.recruit("狙击 高资")
            assert [op["name"] for op in senior["full_match"]] == ["能天使"]
            invalid = await client.recruit("绝对必出")
            assert invalid["error"] == "unknown_tags"
            impossible = await client.recruit("高级资深干员 资深干员")
            assert impossible["success"] is True
            assert impossible["full_match"] == []
            five = await client.recruit("狙击 远程 输出 高资 资深")
            assert five["full_match_available"] is False
            assert five["full_match"] == []
            assert all(len(option["tags"]) <= 3 for option in five["combinations"])
    run(main())


def test_section_missing_is_explicit_and_lists_available_headings():
    def handler(request):
        return httpx.Response(200, json=page("固源岩", "{{道具信息|名称=固源岩}}\n==基础信息==\n材料"))

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await PRTSClient(http_client=http).query("固源岩", section="技能")
            assert result["error"] == "section_not_found"
            assert result["sections"][0]["title"] == "基础信息"
    run(main())


def test_a_dynamic_heading_without_data_is_not_successful_content():
    def handler(request):
        return httpx.Response(200, json=page("固源岩", "{{道具信息|名称=固源岩}}\n==材料掉落==\n{{材料掉落}}"))

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await PRTSClient(http_client=http).query("固源岩", section="材料掉落")
            assert result["success"] is False
            assert result["error"] == "empty_content"
    run(main())
