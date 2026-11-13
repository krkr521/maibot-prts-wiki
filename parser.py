"""Readable PRTS wikitext extraction, adapted from wxgl's arkinfo query flow.

Unlike MediaWiki TextExtracts, this preserves values inside PRTS templates,
including skill levels, specializations, enemy levels, and material quantities.
"""

from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, List, Tuple
import re


HEADINGS = re.compile(r"(?m)^(={2,6})\s*(.*?)\s*\1\s*$")
COMMENTS = re.compile(r"<!--.*?-->", re.S)
WIKILINKS = re.compile(r"\[\[([^\[\]]+)\]\]")
INNER_TEMPLATE = re.compile(r"\{\{([^{}]*)\}\}", re.S)
HTML_TAGS = re.compile(
    r"</?(?:span|div|p|center|small|big|b|i|strong|em|sup|sub|font|section|includeonly|"
    r"noinclude|onlyinclude|table|tbody|thead|tr|td|th|references|ref|gallery|templatestyles)\b[^>]*>",
    re.I,
)
NAVIGATION_TEMPLATES = {
    "pathnav", "pathnav2", "navigator", "toc", "notoc", "spine", "spineid",
    "干员导航", "敌人导航", "关卡导航", "道具导航", "干员页面名", "异格干员",
    "参阅三", "干员攻击范围", "关卡材料掉落", "材料掉落", "干员模型",
}


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(value: str) -> str:
    """Strip the highlighted span markup returned by list=search."""
    parser = _HTMLText()
    parser.feed(value)
    return unescape("".join(parser.parts)).strip()


def _render_template(match: re.Match[str]) -> str:
    parts = match.group(1).split("|")
    name = parts[0].strip()
    lower = name.casefold()
    args = [part.strip() for part in parts[1:]]
    if lower in NAVIGATION_TEMPLATES or name in NAVIGATION_TEMPLATES:
        return ""
    if lower.startswith(('#widget:', '#vardefine:', '#arraydefine:')):
        return ""
    if lower in {"color", "colour", "颜色", "+", "*", "变动数值lite", "变动数值"}:
        return args[-1] if args else ""
    if lower in {"材料消耗", "道具消耗"}:
        return " × ".join(arg for arg in args if arg)
    if lower in {"干员头像", "干员头像2", "道具", "道具图标", "敌人头像", "rarity"}:
        return args[0] if args else ""
    if name in {"PAGENAME", "FULLPAGENAME", "BASEPAGENAME"}:
        return ""
    if lower.startswith(('#if:', '#ifeq:', '#switch:', '#invoke:')):
        # Keep any declared data instead of pretending to evaluate MediaWiki code.
        return "\n" + name + ("\n" + "\n".join(args) if args else "") + "\n"
    if not args:
        return ""
    fields: List[str] = []
    positional: List[str] = []
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            if value.strip():
                fields.append(f"{key.strip()}：{value.strip()}")
        elif arg:
            positional.append(arg)
    if fields:
        return "\n【" + name + "】\n" + "\n".join(fields + positional) + "\n"
    return " / ".join(positional)


def clean_wikitext(value: str) -> str:
    """Remove presentation markup while preserving named template arguments."""
    text = COMMENTS.sub("", value)
    text = re.sub(r"<ref\b[^>]*>.*?</ref\s*>", "", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<nowiki\b[^>]*>(.*?)</nowiki>", r"\1", text, flags=re.I | re.S)
    text = HTML_TAGS.sub("", text)

    def render_link(match: re.Match[str]) -> str:
        parts = match.group(1).split("|")
        if parts[0].casefold().startswith(("file:", "文件:", "image:", "图像:", "分类:", "category:")):
            return ""
        return parts[-1].strip()

    text = WIKILINKS.sub(render_link, text)
    # PRTS nests color/value/material templates. Innermost-first rewriting
    # preserves their display text without losing the parent field's value.
    for _ in range(24):
        text, count = INNER_TEMPLATE.subn(_render_template, text)
        if count == 0:
            break
    text = re.sub(r"\[(https?://[^\s\]]+)\s+([^\]]+)\]", r"\2 (\1)", text)
    text = re.sub(r"'{2,5}", "", text)
    text = re.sub(r"(?m)^\s*\{\|[^\n]*$|^\s*\|[-}]\s*$", "", text)
    text = re.sub(r"(?m)^\s*[|!]\s*", "", text)
    text = text.replace("||", " | ").replace("!!", " | ")
    text = HEADINGS.sub(lambda m: "\n【" + strip_html(m.group(2)) + "】\n", text)
    text = re.sub(r"(?m)^\s*__\w+__\s*$", "", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def sections_from_wikitext(value: str) -> List[Dict[str, Any]]:
    return [
        {"title": strip_html(match.group(2)), "level": len(match.group(1)), "index": str(index)}
        for index, match in enumerate(HEADINGS.finditer(value), start=1)
    ]


def select_section(value: str, requested: str) -> Tuple[str, str]:
    """Select a named heading, including its subordinate headings."""
    matches = list(HEADINGS.finditer(value))
    keyword = requested.strip().casefold()
    candidates = [index for index, match in enumerate(matches) if strip_html(match.group(2)).casefold() == keyword]
    if not candidates:
        candidates = [index for index, match in enumerate(matches) if keyword in strip_html(match.group(2)).casefold()]
    if not candidates:
        raise ValueError(f"未找到章节：{requested}")
    index = candidates[0]
    start = matches[index]
    end = len(value)
    for later in matches[index + 1:]:
        if len(later.group(1)) <= len(start.group(1)):
            end = later.start()
            break
    return value[start.start():end], strip_html(start.group(2))


def select_sections(value: str, requested: str) -> Tuple[str, List[str], List[str]]:
    """Accept one literal heading or several headings, reporting partial matches explicitly."""
    try:
        selected, title = select_section(value, requested)
        return selected, [title], []
    except ValueError:
        pass
    keywords = list(dict.fromkeys(part.strip() for part in re.split(r"[\s,，、;；/+＋|]+", requested) if part.strip()))
    if not keywords or len(keywords) > 8:
        raise ValueError("请指定 1 至 8 个章节，用空格或顿号分隔")
    selected_parts: List[Tuple[str, str]] = []
    missing: List[str] = []
    for keyword in keywords:
        try:
            selected, title = select_section(value, keyword)
        except ValueError:
            missing.append(keyword)
            continue
        # Parent sections already contain their children; avoid repeating large blocks.
        if any(selected in prior for prior, _title in selected_parts):
            continue
        selected_parts = [(prior, prior_title) for prior, prior_title in selected_parts if prior not in selected]
        selected_parts.append((selected, title))
    if not selected_parts:
        raise ValueError("未找到章节：" + "、".join(keywords))
    return "\n\n".join(part for part, _title in selected_parts), [title for _part, title in selected_parts], missing


def detect_category(wikitext: str, categories: List[str]) -> str:
    cats = " ".join(categories)
    if "Charinfo" in wikitext or "分类:干员" in cats:
        return "operator"
    if "敌人信息/" in wikitext or "分类:敌人" in cats:
        return "enemy"
    if "关卡信息" in wikitext or "关卡代号=" in wikitext or "关卡" in cats:
        return "stage"
    if "道具信息" in wikitext or "分类:道具" in cats or "分类:材料" in cats:
        return "item"
    return "page"


def basic_fields(wikitext: str, category: str) -> Dict[str, str]:
    """A compact overview; the full query text retains every source field."""
    keys = {
        "operator": ["干员名", "干员外文名", "职业", "分支", "稀有度", "位置", "标签", "特性", "特性备注", "获得方式"],
        "enemy": ["名称", "种类", "地位级别", "伤害类型", "攻击方式", "行动方式", "描述", "最大生命值", "攻击力", "防御力", "法术抗性"],
        "stage": ["关卡代号", "关卡名", "关卡id", "关卡类型", "推荐等级", "作战消耗", "敌人数量", "部署上限", "关卡描述", "常规掉落"],
        "item": ["名称", "分类", "稀有度", "描述", "用途", "获得方式"],
    }.get(category, [])
    result: Dict[str, str] = {}
    for key in keys:
        match = re.search(r"(?m)^\|" + re.escape(key) + r"\s*=\s*([^\n]*)", wikitext)
        if match:
            text = clean_wikitext(match.group(1))
            if key == "稀有度" and category == "operator" and text.isdigit():
                text = str(int(text) + 1) + "星"
            if text:
                result[key] = text
    return result
