# 第三方依赖与产品名称

本仓库自写代码使用 MIT 许可证。安装时由包管理器获取的依赖及其传递依赖分别遵循各自许可证。

| 依赖 | 版本 | 许可证与来源 | 用途 |
| --- | --- | --- | --- |
| MCP Python SDK | 1.30.0 | MIT，[源仓库](https://github.com/modelcontextprotocol/python-sdk) | stdio MCP 服务与客户端测试 |
| PyYAML | 6.0.3 | MIT，[源仓库](https://github.com/yaml/pyyaml) | 配置样例与自动化测试 |
| ruamel.yaml | 0.18.16 | MIT，[官方说明](https://yaml.dev/doc/ruamel.yaml/) | 安装脚本按 YAML 1.2 安全读写配置 |
| Node.js | 24 系列用于测试 | Node.js 及其内置组件许可证，[许可证](https://github.com/nodejs/node/blob/main/LICENSE) | 交付通知插件的文件、路径与 SQLite API |

DeepSeek Harness 和华为云码道 CodeArts 桌面端由其各自维护者提供。产品名称用于说明集成对象。本项目由社区维护，官方连接器功能依据华为公开文档接入。账号及服务使用受相应产品条款约束。

项目采用人工指示、AI 辅助编写与本机验证的开发方式。原始本机验证资料中的账号信息、任务内容和私有配置保存在开发者本机。公开测试使用独立临时数据。
