# check_seo MCP server

把博客流水线里的 SEO 检查器 `check_seo` 按 **Model Context Protocol** 暴露出来，
任何 MCP 客户端（Hermes、Claude Desktop、Cursor…）都能直接调用。

## 它解决什么

原来 `check_seo` 只能被 Hermes 的插件系统调用（工具面 `blog_tools`）——能力被绑死在一个宿主里。
包成 MCP server 之后，同一个检查器可以给**任何**支持 MCP 的客户端用，不再依赖 Hermes。

## 关键设计：这里不重写任何检查逻辑

```
Hermes 插件（工具面 blog_tools） ──┐
                                    ├──→ handle_check_seo（唯一实现，在 plugins/seo-checker/）
MCP server（stdio）               ──┘
```

本目录只有适配器：`server.py` 通过 `importlib` 载入 `plugins/seo-checker/__init__.py`，
调用里面那份 `handle_check_seo`，**一行检查规则都没有复制**。

这不是靠自觉，是靠测试锁住的：

- `tests/test_seo_mcp_parity.py` —— 奇偶校验：同一输入经两个适配器必须**逐字节同输出**。
  没有这条，日后有人把逻辑复制一份、或改了一边忘了另一边，**两条路径各自看都很正常**，
  没人会发现。
- `tests/test_seo_mcp_protocol.py` —— 真起子进程走 stdio：握手、工具发现、真调用、
  schema 必填项、以及"server 里没复制检查逻辑"的结构检查。

## 跑起来

需要 Python 3.11+ 和 `mcp>=2,<3`：

```bash
uv venv .venv && uv pip install --python .venv/Scripts/python.exe "mcp>=2,<3"
.venv/Scripts/python.exe server.py      # 等 MCP 客户端按 stdio 拉起
```

可选环境变量：

| 变量 | 作用 |
|---|---|
| `SEO_CHECKER_PLUGIN` | 指向 `check_seo` 实现文件的绝对路径；不设时用仓库内 `plugins/seo-checker/__init__.py` |

## 在 Hermes 里注册

```bash
hermes mcp add seo-checker \
  --command "<一个有 mcp 的 python>" \
  --connect-timeout 120 \
  --args "D:/blog-swarm/mcp/seo-checker/server.py"
# --args 必须放在最后；随后按提示回答 Enable all tools? [Y/n]
```

注册后工具名是 **`mcp__seo_checker__check_seo`**（双下划线）。

## 怎么验（别停在"saved OK"）

```bash
hermes mcp list                       # 应见 seo-checker  ✓ enabled
hermes mcp test seo-checker           # ✓ Connected (ms) + ✓ Tools discovered: 1
```

然后让真实 agent 调一次，例如：

```bash
hermes chat -q '调用 mcp__seo_checker__check_seo，title 传「测试标题」，然后把返回 JSON 原样贴出来'
```

## stdio 传输的一个硬约束

**stdout 是协议通道。** 本 server 与被它载入的插件都绝不能 `print()` 到 stdout，
否则协议帧会被破坏、客户端报解析错误。诊断信息一律走 stderr
（`server.py` 启动时会往 stderr 打一行核心实现路径）。`tests/test_seo_mcp_protocol.py`
里有一条测试专门守住这个约束。
