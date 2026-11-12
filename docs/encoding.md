<!-- 本项目编码格式：UTF-8（无 BOM）。 -->
# 编码格式约定

> 定位：本项目**所有文本文件**的编码契约，以及「出现中文乱码时怎么排查」。
> 事实源：`scripts/encoding_check.py`、`scripts/check-encoding.mjs`、`.editorconfig`、`tests/unit/test_encoding_check.py` ｜ 最后核对：2026-11-12

## 1. 一句话约定

全项目文本文件统一 **UTF-8（无 BOM）**，且**第一行**写明本项目使用的编码格式。
这条约定由脚本强制执行，不是建议。

## 2. 每个文件的第一行

声明正文固定为 `本项目编码格式：UTF-8`，注释符随语言变化：

| 文件族 | 合规首行 |
| :--- | :--- |
| `.py` `.yml` `.yaml` `.toml` `.conf` `.sh` `.ps1` `.env` `.gitignore` | `# 本项目编码格式：UTF-8（无 BOM）。` |
| `.ts` `.js` `.mjs` `.scss` | `// 本项目编码格式：UTF-8（无 BOM）。` |
| `.css` | `/* 本项目编码格式：UTF-8（无 BOM）。 */` |
| `.vue` `.md` `.html` | `<!-- 本项目编码格式：UTF-8（无 BOM）。 -->` |
| `.bat` `.cmd` | `@rem 本项目编码格式：UTF-8（无 BOM）。`（必须先于 `@echo off`） |
| `Dockerfile` | `# 本项目编码格式：UTF-8（无 BOM）。` |
| `Jenkinsfile` | `/* 本项目编码格式：UTF-8（无 BOM）。 */` |

两类例外：

- `.json` 不写声明——JSON 规范不允许注释。它仍必须是 UTF-8。
- 可执行脚本第一行必须是 `#!` 时，声明排第二行（门禁认可这种排列）。

## 3. 为什么必须立成机器门禁

中文乱码的成因几乎都不是「代码写错」，而是**同一份文件在不同环境用不同默认编码存盘**。

1. **Windows 默认不是 UTF-8**：控制台与重定向默认 cp936，PowerShell 5.1 的 `>` 写 UTF-16LE。
2. **乱码会「解码成功」**：GBK 字节被当 UTF-8 读进来再存回去，此后任何严格解码都通过，
   只有人眼能看出问题。类型系统和正则都拦不住，只能靠固定残迹识别。常见残迹有三种形态：
   - 替换字符 U+FFFD，或它再被按 latin-1 存一次后的 `ï¿½`； <!-- encoding-check: ignore -->
   - GBK 与 UTF-8 互转两轮的经典三字产物 `锟斤拷`； <!-- encoding-check: ignore -->
   - 中文被按 latin-1 拆读后的 `Ã¤` / `â€` 系列。 <!-- encoding-check: ignore -->

   > 上面三条末尾的 `encoding-check: ignore` 是**行内豁免标记**：说明规则时必然要把残迹
   > 字面量写出来，纯正则分不清「在举例」和「真乱码」，所以由作者显式声明该行是引用。
   > 它只豁免 E4 且只豁免该行——同文件其它行混进真乱码照样报红。见 §4.1。
3. **脚本是重灾区**：脚本输出会进子进程 stdout、日志、报告与 JUnit XML，一处乱码污染整条链路。
   `app/utils/subprocess_util.py` 显式写 `encoding="utf-8", errors="replace"`，正是被这个问题逼出来的。

靠自觉必然回潮，所以固化成提交时必过的门禁。

## 4. 四类检查

| 代号 | 检查内容 | 失败含义 |
| :--- | :--- | :--- |
| E1 | 严格 UTF-8 解码 | 文件被按 GBK/cp936/UTF-16 存盘，报告给出首个非法字节偏移 |
| E2 | 无 BOM | 首字节是 `EF BB BF` |
| E3 | 首行即编码声明 | 新文件漏写文件头，或注释符用错 |
| E4 | 无乱码残迹 | 文件「是合法 UTF-8」但内容已是乱码 |

后端由 `scripts/encoding_check.py` 实现，前端（独立仓库）由 `scripts/check-encoding.mjs` 实现，
判定口径一致——乱码成因与仓库无关。

### 4.1 行内豁免标记

说明「乱码长什么样」的文档，必须把残迹字面量写出来才有意义；纯正则分不清「在举例」与「真乱码」。
为此提供**行内豁免标记**：在含残迹示例的那一行末尾写 `encoding-check: ignore`
（HTML/Markdown 里放进 `<!-- -->` 注释），由作者显式声明这一行是引用。

规则：

- **只豁免 E4，且只豁免所在行**：解码、BOM、首行声明（E1/E2/E3）照常检查；
  同文件其它行混进真乱码，照样报红。
- **没有文件级豁免**：门禁脚本自身也不例外，它的注释只描述残迹形态、不贴字面量。
- 标记必须肉眼可见、可 `grep` 审计。先改脚本加白名单、再让文档「看起来过了」是不允许的——
  那会把真实缺陷藏进豁免表里。

## 5. 出现乱码时的处置顺序

1. **先确认原文件到底是不是 UTF-8**：E1 报的「首个非法字节偏移」是唯一可靠线索。
2. **不要直接整文件重存**：先判断原编码（GBK 还是 UTF-16），用正确编码读出来再写 UTF-8。
3. **已是 U+FFFD 的内容不可逆**：解码失败时原始字节已经丢了，只能按上下文人工确认原意。
   禁止用「看起来像」的字符批量替换冒充修复。
4. **改完跑门禁与自证**：

```bash
python scripts/encoding_check.py
pytest tests/unit/test_encoding_check.py -q
```

## 6. 编辑器侧兜底

`backend/.editorconfig` 与 `frontend/.editorconfig` 固定了 `charset = utf-8` 与 `end_of_line = lf`
（`.bat`/`.cmd` 单独设为 CRLF），项目根设 `root = true`。新环境打开编辑器即生效，
把问题挡在「存盘」这一步，而不是等 CI 拦。

## 7. 检查入口

```bash
python scripts/encoding_check.py            # backend/ 全量 E1–E4
python scripts/encoding_check.py --root ..  # 含容器根与私有目录的跨仓预检
```

```bash
pnpm run check:encoding                     # 前端仓库（在 frontend/ 内执行）
```

两项都已接入 CI：后端的 `test-fast` job 在 pytest 之前执行，前端在类型检查之前执行；
Jenkins 流水线的「静态检查 + 文档门禁」阶段同样调用。

## 8. 相关文档

- [../README.md](../README.md) — 项目入口
- [README.internal.md](README.internal.md) — 文档写作与维护规范
- [scripts/doc_check.py](../scripts/doc_check.py) — 文档结构门禁（与本门禁同批执行）
