"""MaiBot entry point for the PRTS Wiki port (AGPL-3.0-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncio
import re

from maibot_sdk import API, Command, MaiBotPlugin, Tool

from .client import PRTSClient
from .config import PRTSConfig

COMMAND_PATTERN = (
    r"(?i)^(?:@[^\r\n]+?[ \t]+)?/"
    r"(?P<action>prts搜索|prts公招|prts帮助|prts源码|prts|方舟|aknhelp|akngy|aknop|akngz|aknqt|aknoth|akndr|akngq|aknen|aknst)"
    r"(?:[ \t]+(?P<query>[^\r\n]{1,256}))?[ \t]*$"
)
HELP_TEXT = (
    "PRTS 明日方舟百科\n"
    "/prts <词条>：查询干员、敌人、关卡或物品\n"
    "/prts 能天使#技能：只查指定章节\n"
    "/prts搜索 <关键词>：搜索候选条目\n"
    "/prts公招 <标签>：公开招募查询（标签用空格或逗号分隔）\n"
    "兼容 /akngy、/aknop、/akngz、/aknqt、/akndr 等原版指令。\n"
    "平时聊技能、模组、关卡或公招时，bot 可先自行查证再接话，无须说“查一下”。\n"
    "群聊指令默认需要 @机器人。资料来源：https://prts.wiki/\n"
    "本修改版完整源码与许可：/prts源码"
)
CATEGORIES = ["auto", "operator", "enemy", "stage", "item"]


def _error(code: str, content: str) -> dict[str, Any]:
    return {"success": False, "error": code, "content": content}


def _validate_query(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("query 必须是字符串。")
    query = value.strip()
    if not query or len(query) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in query):
        raise ValueError("查询词须为 1–128 字符的单行公开关键词。")
    if "://" in query or query.startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:[\\/]", query):
        raise ValueError("请传入 PRTS 条目名或关键词，不要传入网址或文件路径。")
    return query


def _result_text(result: dict[str, Any]) -> str:
    """Keep the model's readable result alongside machine-readable source fields."""
    parts = []
    title = str(result.get("title") or "").strip()
    content = str(result.get("content") or result.get("message") or "").strip()
    if title:
        parts.append(f"PRTS：{title}")
    if content:
        parts.append(content)
    if "combinations" in result:
        parts.append("当前标签：" + "、".join(result.get("tags", [])))
        if len(result.get("tags", [])) <= 3:
            names = [f"{item['name']}（{item['rarity']}星）" for item in result.get("full_match", [])]
            parts.append("全部所选标签匹配：" + ("、".join(names) if names else "无匹配"))
        parts.append("可选组合（按候选最低星级排序）：")
        for option in result.get("combinations", [])[:12]:
            names = "、".join(f"{item['name']}（{item['rarity']}星）" for item in option.get("operators", []))
            suffix = "（仅列部分，余下请缩小标签范围）" if option.get("truncated") else ""
            parts.append(f"{' + '.join(option.get('tags', []))}：最低{option['min_rarity']}星，{option['total']}名；{names}{suffix}")
        if result.get("note"):
            parts.append(str(result["note"]))
    for item in result.get("results", [])[:10]:
        if isinstance(item, dict):
            parts.append("\n".join(str(item.get(key) or "") for key in ("title", "snippet", "url")).strip())
    url = str(result.get("url") or "").strip()
    if not url:
        url = str(result.get("source_url") or "").strip()
    if url and url not in content:
        parts.append(f"来源：{url}")
    for item in result.get("images", [])[:3]:
        image_url = item.get("url") if isinstance(item, dict) else item
        if image_url:
            parts.append(f"图片：{image_url}")
    if result.get("map_url"):
        parts.append("关卡地图：" + str(result["map_url"]))
    parts.extend(str(item) for item in result.get("warnings", [])[:3])
    if result.get("sections") and result.get("truncated"):
        parts.append("可继续查询的章节：" + "、".join(str(item.get("title") or "") if isinstance(item, dict) else str(item) for item in result["sections"][:30]))
    if result.get("results") == [] and result.get("success") is True:
        parts.append("未找到相关条目。")
    return "\n".join(part for part in parts if part) or "PRTS 没有返回可用内容。"


def _source_text() -> str:
    """Offer only this plugin's distributable source, never runtime config or credentials."""
    root = Path(__file__).resolve().parent
    paths = [root / name for name in (
        "README.md", "NOTICE.md", "LICENSE", "CHANGELOG.md", "_manifest.json", "pyproject.toml", "requirements.txt",
        "help_catalog.json", "webui.json", "config.example.toml", ".gitignore",
    )]
    paths.extend(root.glob("*.py"))
    paths.extend((root / "tests").glob("*.py"))
    chunks = ["PRTS Wiki MaiBot 移植版 v1.0.3 完整对应源码（AGPL-3.0-only）"]
    for path in paths:
        if path.is_file():
            chunks.append(f"\n===== {path.relative_to(root).as_posix()} =====\n" + path.read_text(encoding="utf-8"))
    result = "\n".join(chunks)
    if len(result.encode("utf-8")) > 500000:
        raise ValueError("源码超过消息交付上限，请联系管理员从插件目录提供完整源码包。")
    return result


class PRTSWikiPlugin(MaiBotPlugin):
    config_model = PRTSConfig

    def __init__(self) -> None:
        super().__init__()
        self._client: PRTSClient | None = None
        self._operation_lock = asyncio.Lock()
        self._active = False

    async def on_load(self) -> None:
        self._active = True
        self.ctx.logger.info("PRTS Wiki v1.0.3 已加载：prts_search / prts_query / prts_recruit")

    async def _close_client(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def on_unload(self) -> None:
        self._active = False
        async with self._operation_lock:
            await self._close_client()

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        del config_data, version
        if scope == "self":
            async with self._operation_lock:
                await self._close_client()

    async def _run(self, method: str, **arguments: Any) -> dict[str, Any]:
        if not self._active:
            return _error("inactive", "PRTS 插件当前未运行。")
        try:
            # Bound queueing plus all API requests, and never close an in-use client on a config reload.
            async with asyncio.timeout(38):
                async with self._operation_lock:
                    if not self._active:
                        return _error("inactive", "PRTS 插件当前未运行。")
                    if self._client is None:
                        self._client = PRTSClient(
                            timeout=self.config.network.timeout_seconds,
                            cache_ttl=self.config.network.cache_ttl_seconds,
                            max_concurrency=2,
                        )
                    result = await getattr(self._client, method)(**arguments)
            if not isinstance(result, dict):
                raise TypeError("PRTS 查询结果不是对象")
            # Read-only tools return information to the model; only the command path sends messages.
            result = dict(result)
            result["content"] = _result_text(result)
            return result
        except ValueError as exc:
            return _error("invalid_arguments", str(exc))
        except TimeoutError:
            return _error("timeout", "PRTS 查询超时，请稍后重试或缩小查询范围。")
        except Exception:
            self.ctx.logger.exception("PRTS %s 查询失败", method)
            return _error("query_failed", "PRTS 暂时无法查询，未取得结果；请稍后重试。")

    @Tool(
        "prts_search",
        description=(
            "在普通聊天中自主搜索明日方舟 PRTS Wiki，补齐理解和接话所需的角色、机制或设定背景，"
            "无须用户要求搜索或提到 PRTS。对话已明确在聊方舟，但只知道简称、绰号、模糊条目或需要核实出处时，"
            "结合上下文提取关键词，搜索候选标题后用 prts_query 读取正文；已知准确条目名可直接查询。"
            "候选有歧义时根据上下文消歧，搜索摘要不能代替完整资料。资料用于自然接话，无须先询问要不要查。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PRTS 搜索关键词，如：能天使、源石、7-18"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        visibility="visible",
        timeout_ms=45000,
    )
    async def prts_search(self, query: str = "", limit: int = 5, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        try:
            query = _validate_query(query)
            if type(limit) is not int or not 1 <= limit <= 10:
                raise ValueError("limit 须为 1–10 的整数。")
        except ValueError as exc:
            return _error("invalid_arguments", str(exc))
        return await self._run("search", query=query, limit=limit)

    @Tool(
        "prts_query",
        description=(
            "在普通交谈中主动查证明日方舟资料。聊到干员属性、技能数值与机制、天赋、模组、档案设定、"
            "敌人、关卡或物品时，如果你准备补充、解释或纠正这些具体事实，先查 PRTS 再下结论；"
            "不必等用户明确提问、说查一下或提到 PRTS，闲聊中的陈述、猜测和分歧同样可以自行核查。"
            "例如群友说阿能过载好像是手动的，或讨论某模组有没有改变攻击机制，可直接查能天使的技能或相关模组。"
            "上下文已有与当前问题对应的有效查询结果时直接复用；单纯表达喜好、接梗、喊角色名而不涉及待核实事实时无需查询。"
            "query 填准确词条名，section 优先填技能、天赋、模组、档案等章节，多个章节可用空格或顿号分隔；简称不确定时先 prts_search。"
            "内容截断时按章节继续查；缺失、歧义和网络失败都不能当作事实依据。"
            "把查到的相关事实自然融入聊天，无须询问要不要查，也不必逐条播报查询过程；只返回资料，不自动发送消息。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "干员、敌人、关卡、物品或其他 PRTS 条目名"},
                "category": {"type": "string", "enum": CATEGORIES, "default": "auto"},
                "section": {"type": "string", "description": "可选章节，如技能；多个章节用空格或顿号分隔，如属性 技能 天赋", "default": ""},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        visibility="visible",
        timeout_ms=45000,
    )
    async def prts_query(
        self, query: str = "", category: str = "auto", section: str = "", **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        try:
            query = _validate_query(query)
            if category not in CATEGORIES:
                raise ValueError("category 只允许 auto/operator/enemy/stage/item。")
            if not isinstance(section, str) or len(section) > 64 or any(ord(char) < 32 for char in section):
                raise ValueError("section 须为不超过 64 字符的单行章节名。")
        except ValueError as exc:
            return _error("invalid_arguments", str(exc))
        return await self._run("query", query=query, category=category, section=section.strip(), max_chars=self.config.result.max_chars)

    @Tool(
        "prts_recruit",
        description=(
            "在聊天中主动核实明日方舟公开招募标签组合与可招募干员，数据来自 PRTS Wiki。"
            "群友贴出当前公招标签、讨论这组能出谁、是否保四星或标签怎么选时，可直接从上下文提取标签查询，"
            "无须等用户要求查公招；准备推荐组合前先核对，已有本组有效结果时复用。"
            "tags 传入已明确的 1–5 个标签，以空格或逗号分隔，如治疗 近战位 支援；缺失标签不可猜补。"
            "返回匹配和可选子组合，六星需要高级资深干员标签；不预测抽卡概率，不把候选最低星级说成必出保证。"
        ),
        parameters={
            "type": "object",
            "properties": {"tags": {"type": "string", "description": "当前公招的 1–5 个标签"}},
            "required": ["tags"],
            "additionalProperties": False,
        },
        visibility="visible",
        timeout_ms=45000,
    )
    async def prts_recruit(self, tags: str = "", **kwargs: Any) -> dict[str, Any]:
        del kwargs
        try:
            tags = _validate_query(tags)
        except ValueError as exc:
            return _error("invalid_arguments", str(exc))
        return await self._run("recruit", tags=tags, limit=self.config.result.recruit_limit)

    @API("prts.lookup", description="只读查询 PRTS；与模型工具共用实现，不发送消息。", version="1", public=True, timeout_ms=45000)
    async def lookup(
        self, query: str = "", mode: str = "query", category: str = "auto", section: str = "", limit: int = 5,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del kwargs
        if mode == "query":
            return await self.prts_query(query=query, category=category, section=section)
        if mode == "search":
            return await self.prts_search(query=query, limit=limit)
        if mode == "recruit":
            return await self.prts_recruit(tags=query)
        return _error("invalid_arguments", "mode 只允许 query/search/recruit。")

    @Command(
        "prts_command",
        description="/prts 词条 查询明日方舟百科；/prts搜索 关键词；/prts公招 标签；群聊需要 @机器人。",
        pattern=COMMAND_PATTERN,
        permission="public",
        timeout_ms=45000,
    )
    async def handle_command(
        self, stream_id: str = "", matched_groups: dict[str, Any] | None = None,
        message: dict[str, Any] | None = None, group_id: str = "", **kwargs: Any,
    ) -> tuple[bool, str, bool]:
        raw = message if isinstance(message, dict) else {}
        stream_id = stream_id or str(raw.get("session_id") or "")
        if not stream_id:
            return False, "缺少聊天流", True
        info = raw.get("message_info") or {}
        additional = info.get("additional_config") or {}
        is_group = bool(group_id) or isinstance(info.get("group_info"), dict)
        at_bot = raw.get("is_at") is True or additional.get("at_bot") is True or kwargs.get("is_at") is True
        if is_group and self.config.commands.require_at_in_groups and not at_bot:
            return False, "群聊未 @机器人", True
        groups = matched_groups or {}
        # Match only textual segments so an @ segment cannot become part of a page title.
        segments = raw.get("raw_message")
        if isinstance(segments, list) and any(isinstance(item, dict) and item.get("type") == "at" for item in segments):
            command_text = " ".join(
                str(item.get("data") or "") for item in segments
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()
            matched = re.fullmatch(COMMAND_PATTERN, command_text)
            if matched is None:
                return False, "不是独立的 PRTS 指令", False
            groups = matched.groupdict()
        action = str(groups.get("action") or "prts").casefold()
        query = str(groups.get("query") or "").strip()
        is_source = action == "prts源码"
        if is_source:
            text = await asyncio.to_thread(_source_text)
        elif action in {"aknhelp", "prts帮助"} or not query:
            text = HELP_TEXT
        elif action in {"prts搜索"}:
            text = (await self.prts_search(query, self.config.result.search_limit))["content"]
        elif action in {"akngz", "prts公招"}:
            text = (await self.prts_recruit(query))["content"]
        else:
            category = "operator" if action in {"akngy", "aknop"} else "auto"
            title, _, section = query.partition("#")
            text = (await self.prts_query(title, category, section))["content"]
        if len(text) <= 2500:
            sent = await self.ctx.send.text(text, stream_id)
        else:
            nodes = [
                {"nickname": "PRTS 百科", "segments": [{"type": "text", "content": text[offset:offset + 2200]}]}
                for offset in range(0, len(text), 2200)
            ]
            sent = True
            for offset in range(0, len(nodes), 60):
                if not await self.ctx.send.forward(nodes[offset:offset + 60], stream_id):
                    sent = False
                    break
            if not sent:
                fallback = ("源码消息未完整发送，请重试 /prts源码 或联系管理员提供完整源码包。" if is_source else
                            text[:2200] + "\n内容较长，请用 /prts 词条#章节 缩小范围。")
                sent = await self.ctx.send.text(fallback, stream_id)
        return bool(sent), "PRTS 已回复" if sent else "PRTS 发送失败", True


def create_plugin() -> PRTSWikiPlugin:
    return PRTSWikiPlugin()
