# 来源与许可证

本插件是面向 MaiBot SDK 2.x 的移植版，由 krkr521 维护，参考并适配 wxgl 的
[astrbot_plugin_arkinfo](https://github.com/wxgl/astrbot_plugin_arkinfo)
及其上游 [ark_info_search](https://github.com/wxgl/ark_info_search) 的
PRTS MediaWiki API 查询思路、公开招募 Cargo 数据源与原指令入口。

原项目作者：wxgl。原项目许可证为 GNU AGPL version 3；完整许可见 LICENSE。
本移植以 AGPL-3.0-only 提供，保留原作者归属。

本次修改（2026-11-01）：移除 AstrBot 依赖，使用 MaiBot 生命周期和 Tool/API/Command
声明；增加模型可直接调用的查询、搜索、公招工具；修正公招标签匹配；增加异步请求、
缓存、超时、章节读取、来源链接与明确的失败结果。详细实现与测试保存在本目录。

游戏名称、图片与资料的权利分别属于其权利人。PRTS Wiki 页面和图片遵循站点各自声明；
程序的 AGPL 许可证不为第三方游戏素材另授许可。查询始终返回可追溯的 PRTS 来源。

本修改版完整对应源码：https://github.com/krkr521/maibot-prts-wiki 。
公开版本的源码包与固定版本记录：https://github.com/krkr521/maibot-prts-wiki/releases 。
如通过网络向他人提供本修改版服务，应按 AGPL 第 13 条向使用者提供本修改版对应源码。

v1.0.1：完善工具描述，明确正常交谈中的主动查证时机、结果复用与自然接话方式；
用户无需发出搜索指令。查询与权限逻辑不变。

v1.0.2：支持模型一次指定多个章节，明确报告部分匹配；补充真实 Planner 同款多章节参数回归验证。

v1.0.3（2026-11-13）：首次公开发布，更新仓库信息、安装和验证说明、版本标记及兼容范围；保留原有查询行为、插件 ID 和 AGPL-3.0-only 许可。
