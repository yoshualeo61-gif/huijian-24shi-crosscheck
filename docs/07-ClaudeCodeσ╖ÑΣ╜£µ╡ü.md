# 07 · Claude Code 工作流

**在 Claude Code 里跑，不需要 ANTHROPIC_API_KEY。** Claude 自己就是抽取层。

## 原理

抽取那一步要做的事是：读一段文言，报出人物和行为，附逐字原文。
这件事 Claude Code 里的 Claude 直接就能做，不必再通过 HTTP 调一次模型。

所以流程改成「程序出题 → Claude 作答 → 程序批改」：

```
huijian.py chunks   →  chunks.json        程序：检索、分段
     ↓
  Claude 读 chunks.json，写 extracted.json 抽取（唯一需要模型的一步）
     ↓
huijian.py expand   →  chunks2.json       程序：用人名全库重检索
     ↓
  Claude 读 chunks2.json，写 extracted2.json
     ↓
huijian.py verify   →  report.txt         程序：逐字校验 + 矛盾判定
```

**两阶段不可省。** 详见下面「为什么必须两阶段」。

## 完整命令序列

把下面整段贴给 Claude Code：

```
请按以下步骤执行，每步完成后告诉我结果：

1. python huijian.py fetch
   （首次运行，下载语料约 250MB，2-5 分钟）

2. python huijian.py chunks 郁洲 --also 郁州 -o work/
   （生成 work/chunks.json）

3. 读取 work/chunks.json。对其中每个 chunk 的 "chunk" 字段做信息抽取，
   严格按 docs/07 的「抽取规格」，结果写入 work/extracted.json

4. python huijian.py expand work/
   （用第 3 步抽到的人名全库重检索，生成 work/chunks2.json）

5. 同样对 work/chunks2.json 抽取，写入 work/extracted2.json

6. python huijian.py verify work/
   （校验 + 分析，输出 work/report.txt 和 work/findings.json）

7. python duizhao.py align work/findings.json
```

把 `郁洲 --also 郁州` 换成你要查的题目即可。

## 抽取规格

这是第 3、5 步要遵守的格式。**给 Claude Code 的指令里必须包含这段。**

```
对每个 chunk 抽取人物及其行为，输出 JSON 数组写入指定文件。

铁律：
1. 只记录原文明确写了的。不得补全、演绎、据常识推断。
2. evidence 必须是 chunk 中逐字连续的片段，一个字都不能改——
   包括标点、异体字、以及私用区字符（U+E000–U+F8FF 范围的缺字占位符）。
   **不要"顺手"把生僻字normalize 成常见字，那会导致该条被校验丢弃。**
3. 每条必须带 "ci" 字段，值为该 chunk 的 ci。
4. person 用原文出现的写法（"善明"就写"善明"，不要补成"刘善明"）。
5. 无可抽者跳过该 chunk。宁可空，不可凑。

acts 只能从此表中选（可多选，也可为空数组）：
除授、罢黜、赴任、征战、战胜、战败、死于战、病卒、被杀、
归降、被俘、叛乱、筑城、赈济、上书、出使、逃亡、受封赏

格式：
[{"ci":0,"person":"","title":"","place":"","time":"","acts":[],"evidence":""}]
```

### 关于私用区字符

语料里有 **2,341 个 PUA 字符分布在 740 卷**（平均每卷 3.2 个）。
这些是 Unicode 没有收录的缺字，用 U+E000–U+F8FF 占位。

实测中，一条抽取就是因为把 PUA 字符normalize 掉而被校验拦下。
**复制 evidence 时原样复制，不要修。**

## 为什么必须两阶段

这是实测发现的设计缺陷，不是可选优化。

**以地名检索时，同一个人不会在两方史书都命中。**

张稷在《梁书》的记载不提郁洲，所以单阶段的命中集里只有《魏书》版本。
结果：14 个人全部被标成「仅见于某系」，权重全是 3，输出完全无意义。

改用人名二次检索后：

```
张稷    31段  北朝系:北史/魏书；南朝系:南史/南齐书/梁书   ← 两方都有，可比对
徐玄明   4段  北朝系:魏书                              ← 这才是真独载
垣崇祖   8段  北朝系:魏书；南朝系:南史/南齐书/宋书
```

> **这段实测输出早于 v0.2.1 的立场表修正。** 当时《南史》《北史》被归入
> 南／北朝系；现在它们归「唐修」（理由见 [06-已知局限](06-已知局限.md)）。
> 照现在的表重跑，上面三行的立场分组会变——张稷的北朝系只剩《魏书》，
> 南朝系只剩《南齐书》《梁书》，《南史》《北史》单列为唐修。
> 两阶段检索的必要性不受影响，那才是这一节要说的事。

张稷的权重从 3 跳到 9，终局独载正确触发：
《魏书》四处记其被斩首，南朝系只记其历任官职到镇北将军为止。

## 实测结果参考

郁洲这个题目，阶段一 27 个 chunk，阶段二 98 个。
抽取 22 条有效记载，拦下 2 条伪引（一条是故意测试的虚构，
一条是 PUA normalize 导致的意外）。

徐玄明在《魏书》四卷出现，身份写法各不相同：
无职衔 / 直阁将军 / 军主 / 郁州民。同一人三种身份，本身就是考异线索。

## 两个已知误报，别慌

**一、「除授 ↔ 罢黜」会误报。** 一个人先任后免是正常序列，不是矛盾。
这条规则是故意留在 `huijian.py` 的 `PAIRS` 里的教学用具，让你第一次跑就
明白「所有规则都是待校准的假设」。删掉它：

```python
PAIRS = [("战胜", "战败")]
```

**二、「终局独载」可能是语料不全造成的。**
本仓库的《梁书》只有 12 卷（本纪），列传缺失。张稷本传很可能记了他的死，
只是不在语料里。所以这个信号是**线索不是结论**——必须回查完整本子。

这正是文档 06 说的「独载检测缺基线分布」问题的实例。

## 调试

```bash
# 看检索命中（不需要抽取）
python huijian.py search 郁洲 --also 郁州

# 看某个 chunk 的原文
python3 -c "import json;print(json.load(open('work/chunks.json'))[5]['chunk'])"

# 校验失败时，找出差异在哪一个字
python3 -c "
import json
cs={c['ci']:c for c in json.load(open('work/chunks.json'))}
ev='你的 evidence'
ck=cs[0]['chunk']
for n in range(4,len(ev)+1):
    if ev[:n] not in ck:
        print('斷點:', repr(ev[n-2:n+2]))
        break
"
```

其余见 [04-调试指南](04-调试指南.md)。
