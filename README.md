# PRTS 明日方舟百科（MaiBot 移植版）

由 [krkr521](https://github.com/krkr521) 维护的 MaiBot SDK 2.x 移植版，基于
[wxgl/astrbot_plugin_arkinfo](https://github.com/wxgl/astrbot_plugin_arkinfo) 的查询流程。
使用 PRTS Wiki 官方 MediaWiki API，无需账号、Cookie 或 API Key。源码保留 **AGPL-3.0-only** 许可与原作者归属。

## 让 bot 自主查询

插件启用后直接向 MaiBot 注册三个 `visibility="visible"` 的模型工具，每轮均可直接调用，
不需要先执行 tool_search，也不需要修改全局模型、人设或主程序。

| 工具 | 用途 | 示例参数 |
| --- | --- | --- |
| `prts_search` | 搜索候选条目与来源 | `{"query":"能天使","limit":5}` |
| `prts_query` | 查询条目正文、字段、章节与图片链接 | `{"query":"能天使","category":"operator","section":"技能"}` |
| `prts_recruit` | 查询公招标签及其子组合 | `{"tags":"治疗 近战位 支援"}` |

正常交谈就可以触发自主查证，无需说“查一下”或点名 PRTS。例如：

- “我一直以为阿能过载是手动开的。”——准备解释或纠正机制前先查技能。
- “这个模组到底改了什么，感觉没看出来。”——结合上下文查询对应干员的模组。
- “今天公招是治疗、近战位、支援，这几个看着一般啊。”——可主动核对标签组合。

工具描述要求 bot 在准备补充、解释或纠正具体方舟事实时先核查，再自然接话；
不需要先征询是否搜索，也不必向群友播报每一步工具调用。
单纯表达喜好、接梗或喊名字不触发无意义查询；同一问题已有有效查询结果时复用。
查询工具只返回资料供模型组织回复，不自动向 QQ 发消息。是否调用以及最终表达由当前模型决定。

结果包含 `success`、`content`、PRTS 来源链接。页面查询另有章节、基础字段、revision、更新时间、图片链接和
`truncated`；内容过长时应按 `section` 继续查询，不能把未返回的内容当作不存在。
网络失败、未找到、类型不匹配和歧义都会明确返回错误。

`category` 支持 auto/operator/enemy/stage/item；`section` 例如技能、天赋、模组、档案。
多个章节可写为 `section="属性 技能 天赋"` 或用顿号分隔；结果明确列出匹配与未找到的章节。
默认干员图为精二立绘，无该图时尝试初始立绘；`query="能天使 皮肤1"` 查询编号皮肤图。
敌人和物品图取自 PRTS imageinfo；关卡提供地图来源链接。图片只返回链接，不强制发送或生成图片。

公开招募数据实时取自 PRTS Cargo 可招募名单，支持职业、位置和能力标签；输入最多5个当前标签，
返回实际可选的1–3标签子组合。六星需“高级资深干员”标签。
候选最低星级并非保证，结果不模拟招募时长、标签脱落或概率。

## 指令

- `/prts 能天使`：查询词条。
- `/prts 能天使#技能`：只查技能章节。
- `/prts搜索 源岩`：搜索候选条目。
- `/prts公招 治疗 近战位 支援`：公招查询。
- `/prts帮助` 或 `/aknhelp`：查看用法。
- `/prts源码`：获取本修改版完整对应源码与许可证，不包含本机运行配置。

兼容 `/akngy`、`/aknop`、`/akngz`、`/aknqt`、`/aknoth`、`/akndr`、`/akngq`、`/aknen`、`/aknst`。
群聊指令默认要求真正 @机器人；不限制 bot 在正常对话中使用工具。
较长指令结果使用合并转发；动态 `/help PRTS` 会自动收录本插件。

## 安装与生命周期

需要 Python 3.12+、MaiBot 1.3.18–1.4.x 和 MaiBot Plugin SDK 2.19+（2.x）。离线验证环境为 MaiBot 1.4.3 / SDK 2.22.1。

```bash
git clone https://github.com/krkr521/maibot-prts-wiki.git plugins/local_prts-wiki
```

Python 依赖为 `httpx>=0.28.0`、`beautifulsoup4>=4.12.0` 和宿主 SDK，以 `pyproject.toml` 及同步的 `requirements.txt` 为准。
由宿主安装清单声明的依赖，从 `config.example.toml` 创建运行配置，再通过 WebUI 按名称加载并启用。
公开发布保留插件 ID `local.prts-wiki`，已有调用方不需要改名。

插件随 MaiBot supervisor 加载/卸载/恢复，不新增服务、进程、端口、计划任务或后台作业。
卸载关闭 HTTP 客户端；配置变更等待正在执行的只读查询结束后清理旧客户端。
请求、排队和缓存均有上限，缓存仅在内存中保留5分钟，不写数据库。
日志沿用宿主日志目录。运行配置、日志与聊天数据不包含在仓库或 Release 源码包中。

源码 watcher 可能重载其他插件，建议在 plugins 外完成修改和测试后一次部署。
不要为了本插件重启整个 bot。需要维护时先等待当前只读查询完成，再通过 WebUI 单独停用。

## 验证

```bash
uv sync --group dev
uv run pytest tests -q
uv run ruff check .
```

测试覆盖 API 响应解析、公招规则、章节保留、失败反馈、模型工具声明和群聊指令行为。
离线测试使用模拟网络与隔离数据，不启动 QQ 或发送真实消息。模型在实际群聊中是否自主选用工具，仍取决于当前上下文和模型。

## 来源与许可

原作者和移植说明见 [NOTICE.md](NOTICE.md)，完整许可证见 [LICENSE](LICENSE)，版本变化见 [CHANGELOG.md](CHANGELOG.md)。

本修改版对应源码可从[本仓库](https://github.com/krkr521/maibot-prts-wiki)及 [Releases](https://github.com/krkr521/maibot-prts-wiki/releases) 获取，运行中的插件也提供 `/prts源码`。
PRTS 页面、游戏资料和图片遵循各自权利声明，程序许可证不改变第三方素材的权利。
