"""Async PRTS Wiki API client for MaiBot, without an AstrBot dependency.

The search -> revisions and Cargo recruitment query paths are adapted from
wxgl/astrbot_plugin_arkinfo (AGPL-3.0). See LICENSE and NOTICE in this plugin.
"""

from collections import OrderedDict
from copy import deepcopy
from itertools import combinations
from time import monotonic
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import quote
import asyncio
import json
import re

import httpx

from .parser import basic_fields, clean_wikitext, detect_category, sections_from_wikitext, select_sections, strip_html


API_URL = "https://prts.wiki/api.php"
PAGE_BASE = "https://prts.wiki/w/"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
CATEGORIES = {"auto", "operator", "enemy", "stage", "item"}
TAG_ALIASES = {
    "高资": "高级资深干员", "高级资深": "高级资深干员", "资深": "资深干员",
    "近战": "近战位", "远程": "远程位", "机器人": "支援机械",
}


class PRTSAPIError(RuntimeError):
    """PRTS returned an API error or a response that cannot be interpreted."""


def page_url(title: str) -> str:
    return PAGE_BASE + quote(title.replace(" ", "_"), safe="/")


def _validate_query(query: str) -> str:
    if not isinstance(query, str):
        raise ValueError("查询关键词必须是文本")
    value = query.strip()
    if not value or len(value) > 180:
        raise ValueError("请输入 1 至 180 字的 PRTS 关键词或页面名称")
    if re.search(r"[\x00-\x1f\x7f]", value) or "://" in value or "|" in value:
        raise ValueError("请使用 PRTS 关键词或页面名称，不要输入 URL、控制字符或多页面分隔符")
    return value


def _error(code: str, message: str, query: Any = "", **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": code, "message": message, "query": query, **extra}


class PRTSClient:
    def __init__(
        self,
        timeout: float = 15.0,
        cache_ttl: float = 300.0,
        max_concurrency: int = 2,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.timeout = max(1.0, min(float(timeout), 30.0))
        self.cache_ttl = max(0.0, min(float(cache_ttl), 86400.0))
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=False,
            headers={"User-Agent": "MaiBot-PRTS-Wiki/0.1.0 (personal wiki query; read-only)"},
            limits=httpx.Limits(max_connections=max(1, min(int(max_concurrency), 4)), max_keepalive_connections=2),
        )
        self._semaphore = asyncio.Semaphore(max(1, min(int(max_concurrency), 4)))
        self._cache: OrderedDict[Tuple[Tuple[str, str], ...], Tuple[float, Dict[str, Any]]] = OrderedDict()

    async def close(self) -> None:
        self._cache.clear()
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = {"format": "json", "formatversion": "2", **params}
        key = tuple(sorted((str(k), str(v)) for k, v in query.items()))
        cached = self._cache.get(key)
        if cached is not None and monotonic() - cached[0] <= self.cache_ttl:
            self._cache.move_to_end(key)
            return deepcopy(cached[1])
        async with self._semaphore:
            async with self._client.stream("GET", API_URL, params=query, timeout=self.timeout) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise PRTSAPIError("PRTS API 响应超出 4 MiB 限制，请缩小查询范围")
                try:
                    data = json.loads(body)
                except (UnicodeDecodeError, ValueError) as exc:
                    raise PRTSAPIError("PRTS API 未返回有效 JSON，可能是站点验证或服务异常") from exc
        if not isinstance(data, dict):
            raise PRTSAPIError("PRTS API 返回结构异常")
        if "error" in data:
            error = data["error"]
            if not isinstance(error, dict):
                raise PRTSAPIError("PRTS API 返回异常错误结构")
            raise PRTSAPIError(f"{error.get('code', 'api_error')}：{error.get('info', 'PRTS API 请求失败')}")
        if self.cache_ttl > 0:
            self._cache[key] = (monotonic(), deepcopy(data))
            self._cache.move_to_end(key)
            while len(self._cache) > 32:
                self._cache.popitem(last=False)
        return data

    @staticmethod
    def _network_error(exc: Exception, query: Any) -> Dict[str, Any]:
        if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
            return _error("timeout", "PRTS 查询超时，请稍后重试", query)
        if isinstance(exc, httpx.HTTPStatusError):
            return _error("http_error", f"PRTS API 返回 HTTP {exc.response.status_code}", query)
        if isinstance(exc, httpx.RequestError):
            return _error("network_error", f"PRTS 网络请求失败：{type(exc).__name__}", query)
        return _error("api_error", str(exc), query)

    async def _search(self, query: str, limit: int) -> Dict[str, Any]:
        data = await self._request({
            "action": "query", "list": "search", "srsearch": query,
            "srnamespace": "0", "srlimit": limit, "srprop": "snippet|timestamp",
        })
        block = data.get("query")
        if not isinstance(block, dict) or not isinstance(block.get("search"), list):
            raise PRTSAPIError("PRTS 搜索响应缺少结果列表")
        results = [
            {
                "title": item["title"], "url": page_url(item["title"]),
                "snippet": strip_html(str(item.get("snippet", "")))[:600],
                "timestamp": item.get("timestamp", ""),
            }
            for item in block["search"] if isinstance(item, dict) and isinstance(item.get("title"), str)
        ]
        return {
            "success": True, "query": query, "results": results,
            "total": block.get("searchinfo", {}).get("totalhits", len(results)),
            "source": "PRTS Wiki MediaWiki API", "source_url": page_url("Special:Search") + "?search=" + quote(query),
        }

    async def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        try:
            query = _validate_query(query)
            limit = max(1, min(int(limit), 10))
            async with asyncio.timeout(35):
                return await self._search(query, limit)
        except (ValueError, TypeError) as exc:
            return _error("invalid_argument", str(exc), query)
        except (httpx.HTTPError, PRTSAPIError, TimeoutError) as exc:
            return self._network_error(exc, query)

    async def _page(self, title: str) -> Optional[Dict[str, Any]]:
        data = await self._request({
            "action": "query", "titles": title, "redirects": "1", "converttitles": "1",
            "prop": "info|revisions|categories", "inprop": "url",
            "rvprop": "content|timestamp|ids", "rvslots": "main", "cllimit": "50",
        })
        pages = data.get("query", {}).get("pages")
        if not isinstance(pages, list) or not pages:
            raise PRTSAPIError("PRTS 页面响应缺少页面数据")
        page = pages[0]
        if page.get("missing") or "invalid" in page:
            return None
        revisions = page.get("revisions")
        if not isinstance(revisions, list) or not revisions:
            raise PRTSAPIError("PRTS 页面内容不可用")
        revision = revisions[0]
        content = revision.get("slots", {}).get("main", {}).get("content")
        if not isinstance(content, str):
            raise PRTSAPIError("PRTS 页面缺少可读取的 wikitext")
        return {
            "title": page["title"], "wikitext": content,
            "url": page_url(page["title"]),
            "revision_id": revision.get("revid"), "timestamp": revision.get("timestamp", ""),
            "categories": [str(item["title"]) for item in page.get("categories", []) if "title" in item],
        }

    async def _images(self, title: str, category: str, skin: str) -> List[Dict[str, str]]:
        if category == "operator":
            filenames = [f"文件:立绘 {title} {skin}.png"]
            if skin == "2":
                filenames.append(f"文件:立绘 {title} 1.png")
        elif category == "enemy":
            filenames = [f"文件:头像 敌人 {title}.png"]
        elif category == "item":
            filenames = [f"文件:道具 {title}.png", f"文件:道具 带框 {title}.png"]
        else:
            return []
        data = await self._request({
            "action": "query", "titles": "|".join(filenames), "prop": "imageinfo",
            "iiprop": "url", "redirects": "1",
        })
        pages = data.get("query", {}).get("pages", [])
        if not isinstance(pages, list):
            raise PRTSAPIError("PRTS 图片响应缺少页面数据")
        found: Dict[str, Dict[str, str]] = {}
        for page in pages:
            for info in page.get("imageinfo", [])[:1]:
                url = str(info.get("url", ""))
                # Only expose source-controlled PRTS image URLs, never arbitrary links.
                if re.match(r"^https://(?:[a-z0-9-]+\.)*prts\.wiki/", url):
                    found[page.get("title", "")] = {"title": page.get("title", ""), "url": url}
        ordered = [found[name] for name in filenames if name in found]
        return (ordered if skin.startswith("skin") else ordered[:1])[:3]

    async def query(
        self, query: str, category: str = "auto", section: str = "", max_chars: int = 10000,
    ) -> Dict[str, Any]:
        original = query
        try:
            query = _validate_query(query)
            if category not in CATEGORIES:
                raise ValueError("category 仅支持 auto/operator/enemy/stage/item")
            if not isinstance(section, str) or len(section) > 80:
                raise ValueError("section 必须是 80 字以内的章节名")
            max_chars = max(500, min(int(max_chars), 20000))
            skin = "2"
            skin_match = re.fullmatch(r"(.+?)[\s,，]+(?:时装|皮肤)\s*(\d{1,2})", query)
            if skin_match:
                query = skin_match.group(1).strip()
                skin = "skin" + skin_match.group(2)
            async with asyncio.timeout(35):
                page = await self._page(query)
                matched_by = "exact_or_redirect"
                if page is None:
                    search = await self._search(query, 6)
                    candidates = search["results"]
                    normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", query).casefold()
                    matching = [item for item in candidates if normalized in re.sub(r"[^\w\u4e00-\u9fff]", "", item["title"]).casefold()]
                    if not matching:
                        return _error("not_found", "未找到对应页面；可根据搜索候选重新查询", original, results=candidates)
                    exact = [item for item in matching if normalized == re.sub(r"[^\w\u4e00-\u9fff]", "", item["title"]).casefold()]
                    if len(exact) == 1:
                        matching = exact
                    elif len(matching) > 1:
                        return _error("ambiguous", "关键词对应多个页面，请根据候选标题重新查询", original, results=matching)
                    page = await self._page(matching[0]["title"])
                    matched_by = "title_contains"
                    if page is None:
                        return _error("not_found", "搜索结果中的页面当前不可读取", original, results=candidates)
                wikitext = page["wikitext"]
                actual = detect_category(wikitext, page["categories"])
                if category != "auto" and category != actual:
                    return _error(
                        "category_mismatch", f"页面实际类型为 {actual}，与要求的 {category} 不符", original,
                        title=page["title"], url=page["url"], actual_category=actual,
                    )
                sections = sections_from_wikitext(wikitext)
                selected_section = ""
                selected_sections: List[str] = []
                missing_sections: List[str] = []
                if section.strip():
                    try:
                        selected, selected_sections, missing_sections = select_sections(wikitext, section)
                        selected_section = "、".join(selected_sections)
                    except ValueError as exc:
                        return _error("section_not_found", str(exc), original, title=page["title"], url=page["url"], sections=sections)
                else:
                    selected = wikitext
                content = clean_wikitext(selected)
                if actual == "operator":
                    content = re.sub(r"(?m)^稀有度：(\d+)$", lambda match: "稀有度：" + str(int(match.group(1)) + 1) + "星", content)
                if not re.sub(r"【[^】]*】|\s+", "", content):
                    return _error("empty_content", "页面未包含可提取的文本，请查看原始来源链接", original, title=page["title"], url=page["url"])
                result = {
                    "success": True, "query": original, "title": page["title"], "url": page["url"],
                    "category": actual, "matched_by": matched_by, "fields": basic_fields(wikitext, actual),
                    "section": selected_section, "selected_sections": selected_sections,
                    "missing_sections": missing_sections, "sections": sections,
                    "content": content[:max_chars], "content_chars": len(content), "truncated": len(content) > max_chars,
                    "revision_id": page["revision_id"], "timestamp": page["timestamp"],
                    "images": [], "source": "PRTS Wiki", "warnings": [],
                }
                if result["truncated"]:
                    result["warnings"].append("内容已截断；请使用 section 指定章节继续查询，未返回的部分不可视为不存在")
                if missing_sections:
                    result["warnings"].append("未找到这些章节：" + "、".join(missing_sections) + "；仅返回已匹配章节，不能把缺失部分视为没有相关资料")
                if re.search(r"\{\{(?:#(?:invoke|widget):|材料掉落\s*[}|]|关卡材料掉落\s*[}|])", selected, re.I):
                    result["warnings"].append("本文提取页面原始模板字段，Lua/widget动态生成的数据未展开；未列出的掉落率等信息需查看来源页面，不能据此断言为零或不存在")
                try:
                    result["images"] = await self._images(page["title"], actual, skin)
                except (httpx.HTTPError, PRTSAPIError) as exc:
                    result["warnings"].append("正文已获取，图片查询失败：" + self._network_error(exc, original)["message"])
                if actual == "stage":
                    stage_id = result["fields"].get("关卡id", "")
                    if re.fullmatch(r"[a-z0-9_-]{1,100}", stage_id):
                        result["map_url"] = "https://torappu.prts.wiki/assets/map_preview/" + stage_id + ".png"
                return result
        except (ValueError, TypeError) as exc:
            return _error("invalid_argument", str(exc), original)
        except (httpx.HTTPError, PRTSAPIError, TimeoutError) as exc:
            return self._network_error(exc, original)

    async def recruit(self, tags: Union[str, Sequence[str]], limit: int = 30) -> Dict[str, Any]:
        original = tags
        try:
            tokens = re.split(r"[\s,，、+＋/;；]+", tags.strip()) if isinstance(tags, str) else list(tags)
            if not all(isinstance(tag, str) for tag in tokens):
                raise ValueError("公开招募标签必须是文本")
            selected: List[str] = []
            for tag in tokens:
                tag = tag.strip()
                if tag.endswith("干员") and tag[:-2] in {"先锋", "近卫", "重装", "狙击", "术师", "医疗", "辅助", "特种"}:
                    tag = tag[:-2]
                tag = TAG_ALIASES.get(tag, tag)
                if tag and tag not in selected:
                    selected.append(tag)
            if not 1 <= len(selected) <= 5 or any(len(tag) > 20 for tag in selected):
                raise ValueError("请输入 1 至 5 个公开招募标签，以空格、逗号或加号分隔")
            limit = max(1, min(int(limit), 100))
            async with asyncio.timeout(35):
                data = await self._request({
                    "action": "cargoquery", "tables": "chara,char_obtain", "limit": "500",
                    "fields": "chara.profession,chara.position,chara.rarity,chara.tag,chara.cn",
                    "where": "char_obtain.obtainMethod LIKE '%公开招募%'",
                    "join_on": "chara._pageName=char_obtain._pageName",
                })
                rows = data.get("cargoquery")
                if not isinstance(rows, list) or not rows:
                    raise PRTSAPIError("PRTS 未返回有效的公开招募数据；不能判定为没有匹配干员")
                if len(rows) >= 500:
                    raise PRTSAPIError("PRTS 公开招募数据达到当前查询上限，无法确认名单完整")
                operators: List[Dict[str, Any]] = []
                available = {"高级资深干员", "资深干员"}
                for row in rows:
                    op = row.get("title", {})
                    try:
                        stars = int(op["rarity"]) + 1
                        name, profession, position = str(op["cn"]), str(op["profession"]), str(op["position"])
                    except (KeyError, ValueError, TypeError) as exc:
                        raise PRTSAPIError("PRTS 公开招募干员数据结构异常") from exc
                    op_tags = set(str(op.get("tag", "")).split()) | {profession, position}
                    available.update(op_tags)
                    operators.append({"name": name, "rarity": stars, "profession": profession, "position": position, "tags": sorted(op_tags), "url": page_url(name)})
                invalid = [tag for tag in selected if tag not in available]
                if invalid:
                    return _error("unknown_tags", "不识别的公开招募标签：" + "、".join(invalid), original, available_tags=sorted(available))

                def matching(chosen: Sequence[str]) -> List[Dict[str, Any]]:
                    if "高级资深干员" in chosen and "资深干员" in chosen:
                        return []
                    required = set(chosen) - {"高级资深干员", "资深干员"}
                    result: List[Dict[str, Any]] = []
                    for op in operators:
                        if "高级资深干员" in chosen:
                            if op["rarity"] != 6:
                                continue
                        elif "资深干员" in chosen:
                            if op["rarity"] != 5:
                                continue
                        elif op["rarity"] == 6:
                            continue
                        if required.issubset(op["tags"]):
                            result.append(op)
                    return sorted(result, key=lambda op: (-op["rarity"], op["name"]))

                full_match_available = len(selected) <= 3
                all_matches = matching(selected) if full_match_available else []
                options: List[Dict[str, Any]] = []
                for count in range(1, min(len(selected), 3) + 1):
                    for chosen in combinations(selected, count):
                        matches = matching(chosen)
                        if matches:
                            options.append({
                                "tags": list(chosen), "min_rarity": min(op["rarity"] for op in matches),
                                "total": len(matches), "operators": matches[:min(limit, 8)], "truncated": len(matches) > min(limit, 8),
                            })
                options.sort(key=lambda option: (-option["min_rarity"], option["total"], -len(option["tags"])))
                return {
                    "success": True, "query": original, "tags": selected,
                    "full_match_available": full_match_available,
                    "full_match": all_matches[:limit], "total": len(all_matches), "truncated": len(all_matches) > limit,
                    "combinations": options[:12], "combination_total": len(options),
                    "source": "PRTS Wiki Cargo API", "url": page_url("公开招募"),
                    "note": "按当前 PRTS 可公开招募名单匹配职业、位置及能力标签；六星仅在含高级资深干员时列出。游戏最多选择3个标签，输入4至5个时仅供子组合分析，full_match不可选用。未模拟招募时长、标签脱落或概率，候选最低星级不等于必出保证。",
                }
        except (ValueError, TypeError) as exc:
            return _error("invalid_argument", str(exc), original)
        except (httpx.HTTPError, PRTSAPIError, TimeoutError) as exc:
            return self._network_error(exc, original)
