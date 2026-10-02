# 互見 huijian — 史料交叉比對

把散在二十四史各处的记载并排摆出，标出彼此对不上的地方。

**它不告诉你真相，它告诉你哪两条记载对不上。**

---

## 在 Claude Code 里跑（推荐，不需要 API key）

Claude 自己就是抽取层。把下面整段贴给 Claude Code：

```
请按以下步骤执行，每步完成后告诉我结果。抽取规格见 docs/07-ClaudeCode工作流.md。

1. python huijian.py fetch
2. python huijian.py chunks 郁洲 --also 郁州 -o work/
3. 读 work/chunks.json，按抽取规格做抽取，写入 work/extracted.json
4. python huijian.py expand work/
5. 读 work/chunks2.json，同样抽取，写入 work/extracted2.json
6. python huijian.py verify work/
7. python duizhao.py align work/findings.json
```

**详见 [docs/07-ClaudeCode工作流.md](docs/07-ClaudeCode工作流.md)** —— 含抽取规格全文、
PUA 字符陷阱、两阶段检索的必要性。

## 或者用 API 模式

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-...
python huijian.py run 郁洲 --also 郁州 -o out/ --dry-run   # 先估成本
python huijian.py run 郁洲 --also 郁州 -o out/ --limit 10
```

---

## 核心设计：模型读，代码判

模型只做两件事：抽取（读文言，报人物和行为，附逐字原文）、人物归一。

其余全是普通 Python——矛盾检测、独载检测、纪年换算、事件对齐。

| | 错误形态 | 能否发现 |
|---|---|---|
| 模型抽错 | 引文对不上原文 | 能，程序自动拦截并计数 |
| 代码判错 | 规则定得不对 | 能，逻辑写在那儿，可以指着说这条错 |
| 模型直接下判断 | 流畅但错误的分析 | **不能** |

第三种是这个设计要避免的。不能被指着反驳的「洞见」没有研究价值。

## 它能做什么

- 把同一人散在不同书、不同门类的记载聚合起来
- 标出跨立场的记载冲突、纪年歧异、单方独载、终局独载
- 同一事件在两方史书中的措辞差异（春秋笔法）
- 每条附逐字原文和卷次，可回查

## 它做不到什么

- 恢复没写下来的东西
- 回答「为什么」——动机类判断连专家之间一致性都只有 κ 0.27–0.32
- 只有一方记载时工作（立场轴塌陷）
- 给出可信度——报告里的「线索权重」只是排序，不是概率

## 语料

二十四史全文，来自 [hunterhug/china-history](https://github.com/hunterhug/china-history)。

**2,413 卷 / 2,205 万字**（已排除 621 个现代白话译文文件）。

已知缺口：《宋书》《南齐书》缺列传；《梁书》《陈书》《南史》《北史》只有部分卷；
时间止于《明史》。检索前先确认覆盖：

```bash
python3 -c "import pathlib;[print(d.name,len(list(d.iterdir()))) for d in sorted(pathlib.Path('corpus').iterdir())]"
```

**语料不随本仓库分发**，由 `fetch` 自行下载。原文属公有领域，
但现代点校本的句读与校勘可能有著作权，上游仓库未附授权声明，使用者自负。

## 文件

| 路径 | 内容 |
|---|---|
| `huijian.py` | 检索、分段、抽取、校验、矛盾检测 |
| `duizhao.py` | 纪年换算、跨立场事件对齐、立场用语检测 |
| `ui/shiyuan_reader.jsx` | 标注界面（React artifact） |
| `samples/` | 郁洲案例的现成史料与测试数据 |
| `互見_介紹.pptx` | 介绍幻灯片。**注意：里面写的 3,034 卷 / 3,021 万字是旧数据，含译文；正确值见上** |

## 文档

| 文档 | 什么时候读 |
|---|---|
| [07-ClaudeCode工作流](docs/07-ClaudeCode工作流.md) | **在 Claude Code 里跑就读这个** |
| [01-安装与上手](docs/01-安装与上手.md) | 第一次跑 |
| [02-使用手册](docs/02-使用手册.md) | 查命令和参数 |
| [03-实现说明](docs/03-实现说明.md) | 要改代码时 |
| [04-调试指南](docs/04-调试指南.md) | 出问题时 |
| [05-标注指南模板](docs/05-标注指南模板.md) | 准备建金标准时 |
| [06-已知局限](docs/06-已知局限.md) | 决定要不要用之前 |

## 三条使用纪律

1. **每条结论都要回查原文。** 工具告诉你去哪看，不告诉你结论。
2. **别指望挖出新史料，指望读得快。** 优势是不知疲倦，不是更聪明。
3. **「为什么」类的判断不要信。** 谁、何时、何地可靠；动机不可靠。

## 当前状态 v0.2

**一把能用的刀，不是一篇能发的论文。**

所有判定参数（阈值、立场词表、互斥集）**未经标注验证**。
`PAIRS` 里的 `("除授","罢黜")` 是**故意留的错误规则**，会误报——
一个人先任后免是正常序列。留着是为了让你第一次跑就明白规则需要校准。
正式使用前删掉它。

抽取精度未测。要从「能用」到「可发表」，缺的是任务定义、标注框架、
一致性数据——见文档 05 和 06。
