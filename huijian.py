#!/usr/bin/env python3
"""
互見 huijian — 二十四史交叉比對流水線

模型只做抽取；矛盾、歧異、獨載全部由確定性程式碼判定。
凡模型引文未在原文逐字出現者一律丟棄。

用法:
    python huijian.py fetch                    # 下載並轉換語料 (~250MB)
    python huijian.py search 郁洲               # 變體擴展檢索
    python huijian.py dossier 郁洲 -o out/      # 按立場導出史料
    python huijian.py run 郁洲 -o out/          # 全流程 (需 ANTHROPIC_API_KEY)
"""

import argparse, collections, html, json, os, pathlib, random, re, subprocess, sys
import urllib.request

# Windows 預設以本地代碼頁寫 stdout，一旦重定向到檔案，中文即 UnicodeEncodeError。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):     # 非 TextIOWrapper，或已被接管
        pass

REPO = "https://github.com/hunterhug/china-history.git"
RAW = pathlib.Path("china-history")
COR = pathlib.Path("corpus")
MODEL = "claude-sonnet-4-6"

# ── 立場表：決定「單方獨載」怎麼算 ────────────────────────────
STANCE = {
    "宋书": "南朝系", "南齐书": "南朝系", "梁书": "南朝系", "陈书": "南朝系",
    "魏书": "北朝系", "北齐书": "北朝系", "周书": "北朝系",
    # 《南史》《北史》為李延壽唐修，係刪削《宋書》《南齊書》《魏書》等而成。
    # 計入南／北朝系會把派生本當成獨立證人，虛增立場軸的獨立性，
    # 獨載檢測與跨立場對齊都會因此失真。
    "南史": "唐修", "北史": "唐修", "晋书": "唐修", "隋书": "唐修",
    "旧唐书": "五代宋修", "新唐书": "五代宋修",
    "旧五代史": "宋修", "新五代史": "宋修", "宋史": "元修",
    "辽史": "元修", "金史": "元修", "元史": "明修", "明史": "清修",
    "史记": "漢修", "汉书": "漢修", "后汉书": "南朝修", "三国志": "晋修",
}

# ── 行為類型與互斥規則（確定性層）────────────────────────────
ACTS = ["除授", "罢黜", "赴任", "征战", "战胜", "战败", "死于战", "病卒",
        "被杀", "归降", "被俘", "叛乱", "筑城", "赈济", "上书", "出使",
        "逃亡", "受封赏"]
TERMINAL = {"死于战", "病卒", "被杀", "归降", "被俘"}   # 任兩者並存＝硬矛盾
PAIRS = [("战胜", "战败"), ("除授", "罢黜")]

# 終局用語：供基線統計用的保守詞表。只收歧義小的，寧漏不濫 ——
# 基線本身若充滿誤判，拿它校準別的判斷就毫無意義。
TERMINAL_LEX = [
    "卒", "薨", "殂",                                      # 病卒
    "見殺", "见杀", "伏誅", "伏诛", "賜死", "赐死",
    "坐誅", "坐诛", "梟首", "枭首", "斬之", "斩之", "遇害",   # 被殺
    "戰死", "战死", "沒於陣", "没于阵", "力戰而死", "力战而死",  # 死於戰
    "被擒", "見擒", "见擒", "為所執", "为所执",              # 被俘
    "內附", "内附", "歸降", "归降", "來降", "来降",
    "舉城降", "举城降",                                     # 歸降
]

# 一個 chunk 可能由同卷中數個不相鄰的窗口拼成，接縫處插入此標記。
# 校驗要求引文完整落在單一窗口內 —— 跨縫即拼接，一律丟棄。
SEAM = "\n……【中略】……\n"

# 命中項。start 是 ctx 在該卷正規化全文中的字元偏移，src 是該卷語料路徑；
# 兩者合起來就是引文的可回查地址。
Hit = collections.namedtuple("Hit", "book juan stance term ctx start src")


# ── 語料 ──────────────────────────────────────────────────
def html_to_text(p):
    s = p.read_text(encoding="utf-8", errors="ignore")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = html.unescape(s)
    junk = ("←", "首页", "目录", "下一节", "上一节", "原文", "返回", "阿星星")
    keep = [l.strip() for l in s.split("\n")]
    return "\n".join(l for l in keep if l and not any(l.startswith(j) for j in junk))


def cmd_fetch(_):
    if not RAW.exists():
        print("clone …", file=sys.stderr)
        subprocess.run(["git", "clone", "--depth", "1", "-q", REPO], check=True)
    COR.mkdir(exist_ok=True)
    stats = []
    for book in sorted(RAW.iterdir()):
        if not book.is_dir() or book.name.endswith("-白话"):
            continue
        files = [f for f in sorted(book.rglob("*.html")) if f.name != f"{book.name}.html"]
        if not files:
            continue
        d = COR / book.name
        d.mkdir(exist_ok=True)
        n = c = 0
        for f in files:
            if any(w in f.name or w in f.parent.name for w in ("译文", "譯文", "白话", "白話", "段译")):
                continue                      # 現代白話翻譯，不入語料
            t = html_to_text(f)
            if len(t) < 200:
                continue
            name = re.sub(r"[^\w\u4e00-\u9fff-]", "", f.parent.name + "_" + f.stem)
            (d / f"{name}.txt").write_text(t, encoding="utf-8")
            n += 1
            c += len(t)
        stats.append((book.name, STANCE.get(book.name, "?"), n, c))
    stats.sort(key=lambda x: -x[3])
    print(f"{'書':8s} {'立場':8s} {'卷':>5s} {'字':>11s}")
    for b, s, n, c in stats:
        print(f"{b:8s} {s:8s} {n:5d} {c:11,d}")
    print(f"{'合計':8s} {'':8s} {sum(x[2] for x in stats):5d} {sum(x[3] for x in stats):11,d}")


# ── 變體擴展 ───────────────────────────────────────────────
def variants(term, extra=()):
    out = {term, *extra}
    try:
        from opencc import OpenCC
        out |= {OpenCC("s2t").convert(term), OpenCC("t2s").convert(term)}
    except ImportError:
        print("提示: pip install opencc-python-reimplemented 可自動繁簡擴展", file=sys.stderr)
    return sorted(out)


def norm_text(p):
    """卷的正規化全文：去掉所有空白，使字元偏移成為穩定的地址。"""
    return re.sub(r"\s+", "", p.read_text(encoding="utf-8"))


def scan(terms, window=200):
    """回傳 [Hit]，去重。

    檢索在正規化全文上進行，故 start 是上下文在該卷正規化文本中的字元偏移。
    這個偏移就是引文的地址，可據以重新切片核對。
    """
    seen, hits = set(), []
    for f in sorted(COR.rglob("*.txt")):
        if any(w in f.name for w in ("译文", "譯文", "白话", "白話", "段译")):
            continue
        book = f.parts[1]
        s = norm_text(f)
        for t in terms:
            for m in re.finditer(re.escape(t), s):
                a, b = max(0, m.start() - window), min(len(s), m.end() + window)
                ctx = s[a:b]
                juan = re.sub(r"-原文|第.+?章-|原文版|段译", "", f.stem)
                # 去重限於同一部書之內：摺疊重疊的檢索窗口，以及語料裡
                # 卷名不同而內容相同的重複檔（如 南史_卷一 與 南史_-卷一）。
                # 不可做成全局 key——《南史》刪削《宋書》而成，成段雷同者
                # 只會留下排序在前的那一本，被丟掉的往往正是原始史源，
                # 交叉比對因此少掉一方，並誤報「單方獨載」。
                k = (book, ctx[:45])
                if k in seen:
                    continue
                seen.add(k)
                hits.append(Hit(book, juan, STANCE.get(book, "?"), t, ctx,
                                a, f.as_posix()))
    return hits


def cmd_search(a):
    terms = variants(a.term, a.also or ())
    print(f"檢索詞: {'、'.join(terms)}\n", file=sys.stderr)
    hits = scan(terms, a.window)
    by_stance = {}
    for h in hits:
        by_stance.setdefault(h.stance, []).append(h)
    for st, rows in sorted(by_stance.items(), key=lambda x: -len(x[1])):
        print(f"\n═══ {st} · {len(rows)} 段 ═══")
        for h in rows:
            print(f"\n【{h.book}·{h.juan}】({h.term} @{h.start})\n …{h.ctx}…")
    print(f"\n共 {len(hits)} 段", file=sys.stderr)


def cmd_dossier(a):
    terms = variants(a.term, a.also or ())
    hits = scan(terms, a.window)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    groups = {}
    for h in hits:
        groups.setdefault(h.stance, []).append(h)
    for st, rows in groups.items():
        p = out / f"{a.term}_{st}.txt"
        with p.open("w", encoding="utf-8") as fh:
            for h in rows:
                fh.write(f"《{h.book}·{h.juan}》@{h.start}\n…{h.ctx}…\n\n")
        print(f"{p}  {len(rows)} 段  {sum(len(r.ctx) for r in rows):,} 字")


# ── 模型層：只做抽取 ───────────────────────────────────────
EXTRACT_PROMPT = """你是史料抽取器。只抽取，不判断，不推论。

铁律：
1. 只记录原文明确写了的。不得补全、演绎、据常识推断。
2. evidence 必须是原文中逐字连续的片段，一字不改。
3. 无可抽者返回 []。宁可空，不可凑。
4. person 用原文出现的写法，不要自行补全姓氏。
5. 史料中若出现「……【中略】……」，表示它前后的文字在原书里并不相邻。
   evidence 不得跨越这个标记 —— 跨越即拼接，会被校验丢弃。

acts 只能选自：{acts}

只输出 JSON 数组，无 markdown，无解释：
[{{"person":"","title":"","place":"","time":"","acts":[],"evidence":""}}]

史料：
<<<{chunk}>>>"""


def call_api(prompt, key):
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": MODEL, "max_tokens": 2000,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.load(r)
    txt = "".join(b["text"] for b in d["content"] if b["type"] == "text")
    txt = txt.replace("```json", "").replace("```", "").strip()
    i, j = txt.find("["), txt.rfind("]")
    return json.loads(txt[i:j + 1]) if i >= 0 and j > i else []


def chunks(text, n=1400):
    out, buf = [], ""
    for seg in re.split(r"(?<=[。！？；\n])", text):
        if len(buf) + len(seg) > n and buf:
            out.append(buf)
            buf = seg
        else:
            buf += seg
    if buf.strip():
        out.append(buf)
    return out


def extract(hits, key, limit=0, dry=False, debug=False):
    """回傳 (rows, 攔截數)。偏移重切是防杜撰的硬保證。"""
    rows, dropped, calls = [], 0, 0
    cks = _mkchunks(hits)
    for c in cks:
        if limit and calls >= limit:
            print(f"  達到 --limit {limit}，停止呼叫", file=sys.stderr)
            return rows, dropped
        calls += 1
        print(f"  [{c['ci'] + 1}/{len(cks)}] {c['book']}·{c['juan']}"
              f"  ({len(c['chunk'])}字，{len(c['segments'])}段)", file=sys.stderr)
        if dry:
            continue
        try:
            items = call_api(
                EXTRACT_PROMPT.format(acts="、".join(ACTS), chunk=c["chunk"]), key)
        except Exception as e:
            print(f"    ! API 失敗: {e}", file=sys.stderr)
            continue
        if debug:
            print(f"    模型返回 {len(items)} 條", file=sys.stderr)
        for it in items:
            ev = (it.get("evidence") or "").strip()
            loc = locate(ev, c)
            if loc and it.get("person"):
                it["evidence"] = ev
                it.update(book=c["book"], juan=c["juan"], stance=c["stance"],
                          src=c.get("src"), ev_start=loc[0], ev_end=loc[1])
                rows.append(it)
            else:
                dropped += 1
                if debug:
                    print(f"    ✗ 攔截: 「{ev[:30]}」", file=sys.stderr)
    return rows, dropped


# ── 基線：該書記同類人之死的正常比率 ──────────────────────
def _attributable(s, nm, m, window, others):
    """名字附近的終局用語是否可歸於本人。

    若名字與該用語之間夾著另一個對照名，這用語更可能是那個人的 ——
    本紀一段之內常列數人數死，不加這個條件，基線會被鄰人的死灌水，
    而基線一高，獨載的沉默就都顯得正常，遮掉真信號。
    """
    a = max(0, m.start() - window)
    b = min(len(s), m.end() + window)
    for w in TERMINAL_LEX:
        for g in re.finditer(re.escape(w), s[a:b]):
            p0, p1 = a + g.start(), a + g.end()
            if p1 <= m.start():
                between = s[p1:m.start()]
            elif p0 >= m.end():
                between = s[m.end():p0]
            else:
                between = ""
            if any(o in between for o in others):
                continue                     # 中間隔著別人，不算本人的
            return True
    return False


def terminal_rate(names, window=120):
    """每部書的「終局著錄率」。

    對每個（人名, 書）組合：該書若提及此人，其名前後 window 字內是否出現
    終局用語。比率 ＝ 有終局者 ／ 提及者。

    這是獨載檢測一直缺的那塊。「某書記其人而不記其終」要成為諱飾的證據，
    先得知道該書記同類人之死的正常比率有多高 —— 比率本就低的書，
    沉默什麼也說明不了。全程確定性統計，不需要標註，也不呼叫模型。

    歸屬用「中間不得夾著別人」的規則（見 _attributable）。這仍是近似：
    詞表只收歧義小的詞，窗口也是拍的，所以比率有誤差。但它是**可審的**
    近似 —— 規則寫在這兒，可以指著說這條不對。
    """
    stat = {}
    for f in sorted(COR.rglob("*.txt")):
        if any(w in f.name for w in ("译文", "譯文", "白话", "白話", "段译")):
            continue
        book = f.parts[1]
        s = norm_text(f)
        for nm in names:
            if nm not in s:
                continue
            d = stat.setdefault(book, {"seen": set(), "term": set()})
            d["seen"].add(nm)
            others = [o for o in names if o != nm and o not in nm]
            for m in re.finditer(re.escape(nm), s):
                if _attributable(s, nm, m, window, others):
                    d["term"].add(nm)
                    break
    out = {}
    for book, d in stat.items():
        n, k = len(d["seen"]), len(d["term"])
        out[book] = {"n": n, "k": k, "rate": (k / n) if n else None}
    return out


def _names_from_work(w):
    """工作目錄裡能找到的人名，供基線的對照群體用。"""
    names = set()
    for fn in ("findings.json", "extracted.json", "extracted2.json"):
        p = w / fn
        if not p.exists():
            continue
        data = json.load(open(p, encoding="utf-8"))
        for it in data:
            if it.get("person"):
                names.add(it["person"])
            for h in it.get("hits") or []:
                if h.get("person"):
                    names.add(h["person"])
    return sorted(names)


def cmd_baseline(a):
    """算基線並寫入 work/baseline.json，供 verify 校準獨載權重。"""
    w = pathlib.Path(a.out)
    names = _names_from_work(w)
    if not names:
        sys.exit(f"{w} 裡找不到人名（需先跑 verify 或抽取）")
    print(f"對照群體 {len(names)} 人：{'、'.join(names[:10])}"
          f"{'…' if len(names) > 10 else ''}\n", file=sys.stderr)
    base = terminal_rate(names, a.window)
    _dump(w / "baseline.json", base)
    print(f"{'書':10s} {'提及':>5s} {'記其終':>6s} {'著錄率':>7s}")
    for b, d in sorted(base.items(), key=lambda x: -(x[1]["rate"] or 0)):
        print(f"{b:10s} {d['n']:5d} {d['k']:6d} {d['rate']:7.0%}")
    print(f"\n→ {w}/baseline.json　下次 verify 會自動讀取並據以校準獨載權重",
          file=sys.stderr)


# ── 知異：已知集合減法 ────────────────────────────────────
# 系統分不清哪條是新發現、哪條是錢大昕兩百年前就寫過的。沒有這層掩碼，
# 任何「新發現」的說法都不成立 —— 所以寧可讓報告大聲說「新穎性未知」,
# 也不要讓它默不作聲地任人誤會。
#
# 索引是一個 JSON 陣列，每筆：
#   {"person": "張稷", "book": "魏書", "juan": "卷六十一",
#    "topic": "卒年", "source": "錢大昕《廿二史考異》卷三十", "note": "…"}
# person 必填，其餘可空；book／juan 給了就一併比對。
# 倉庫**不附任何條目** —— 考異原書需自行取得並錄入，代錄即是編造。
ZHIYI_FILES = ("zhiyi.json", "data/zhiyi.json")


def load_zhiyi(w):
    """從工作目錄或倉庫根目錄讀知異索引。沒有就回 None（新穎性未知）。"""
    for base in (pathlib.Path(w), pathlib.Path(".")):
        for fn in ZHIYI_FILES:
            p = base / fn
            if p.exists():
                try:
                    data = json.load(open(p, encoding="utf-8"))
                except Exception as e:
                    print(f"知異索引讀取失敗 {p}: {e}", file=sys.stderr)
                    return None
                return [e for e in data if e.get("person")]
    return None


def mark_known(res, index):
    """給每個人物掛上已被考異述及的條目。只標，不刪。"""
    hit = 0
    for p in res:
        got = []
        for e in index:
            if e["person"] != p["person"]:
                continue
            bks = {h["book"] for h in p["hits"]}
            jns = {h["juan"] for h in p["hits"]}
            if e.get("book") and e["book"] not in bks:
                continue
            if e.get("juan") and e["juan"] not in jns:
                continue
            got.append(e)
        if got:
            hit += 1
            p["known"] = got
    return hit


# ── 置換檢驗：立場軸是真信號還是巧合 ──────────────────────
def _flag_kind(k):
    """旗標的類別名。

    必須把「」裡的立場名與（）裡的行為名都剝掉：否則「僅見於「南朝系」」
    與「僅見於「北朝系」」會算成兩種信號，置換後的虛無分布隨之碎掉，
    檢驗就失去意義。
    """
    k = re.sub(r"「[^」]*」", "", k)
    k = re.split(r"[：（(]", k)[0]
    return k.strip() or "其他"


def flag_counts(rows, stances):
    c = collections.Counter()
    for p in analyse(rows, stances):
        for _, k, _ in p["flags"]:
            c[_flag_kind(k)] += 1
    return c


def cmd_null(a):
    """把立場標籤在書之間打亂，看各類信號還剩多少。

    立場軸是全部跨立場信號的前提。若打亂標籤後信號量不降，那些信號
    就不是立場差異造成的 —— 權重也就沒有意義。這是最便宜的一道偽證檢驗，
    而且不需要金標準。
    """
    w = pathlib.Path(a.out)
    p = w / "findings.json"
    if not p.exists():
        sys.exit(f"找不到 {p}（先跑 verify）")
    data = json.load(open(p, encoding="utf-8"))
    rows = [h for it in data for h in (it.get("hits") or [])] or data
    rows = [dict(r) for r in rows if r.get("person") and r.get("book")]
    if not rows:
        sys.exit("findings.json 裡沒有可用記載")

    books = sorted({r["book"] for r in rows})
    by_book = {r["book"]: r["stance"] for r in rows}
    stances = sorted({r["stance"] for r in rows})
    if len(stances) < 2:
        sys.exit("只有一種立場，置換檢驗無意義（立場軸已塌陷）")

    obs = flag_counts(rows, stances)
    rnd = random.Random(a.seed)
    labels = [by_book[b] for b in books]
    null = collections.defaultdict(list)
    for _ in range(a.iters):
        sh = labels[:]
        rnd.shuffle(sh)
        amap = dict(zip(books, sh))
        perm = [dict(r, stance=amap[r["book"]]) for r in rows]
        c = flag_counts(perm, sorted(set(sh)))
        for k in set(obs) | set(c):
            null[k].append(c.get(k, 0))

    print(f"置換檢驗　{a.iters} 次　{len(books)} 部書　{len(rows)} 條記載")
    print(f"立場標籤在書之間打亂（保留書內結構）\n")
    print(f"{'信號':14s} {'實測':>5s} {'虛無均值':>9s} {'虛無95%':>8s} {'p':>7s}")
    out = {}
    for k in sorted(set(obs) | set(null), key=lambda x: -obs.get(x, 0)):
        v = sorted(null.get(k, [0]))
        o = obs.get(k, 0)
        mean = sum(v) / len(v)
        p95 = v[min(len(v) - 1, int(0.95 * len(v)))]
        pval = (sum(1 for x in v if x >= o) + 1) / (len(v) + 1)
        mark = "" if pval > 0.05 else "  *"
        print(f"{k:14s} {o:5d} {mean:9.2f} {p95:8d} {pval:7.3f}{mark}")
        out[k] = {"observed": o, "null_mean": mean, "null_p95": p95, "p": pval}
    _dump(w / "null_test.json", out)
    print(f"\n* ＝ 打亂後不易出現，立場軸確有貢獻。無星者不可當立場證據引用。")
    print(f"\n→ {w}/null_test.json", file=sys.stderr)


# ── 校驗閘：按偏移重新切片 ────────────────────────────────
def locate(ev, c):
    """引文在原卷中的絕對地址 (start, end)，不合格返回 None。

    要求引文完整落在單一 segment 之內。一個 chunk 可能由同卷中數個不相鄰的
    窗口拼成，若引文橫跨接縫，它在 chunk 裡看似連續，在原書裡卻不是 ——
    這正是子串測試 `ev in chunk` 攔不住的那一類拼接。
    """
    if len(ev) < 4:
        return None
    segs = c.get("segments")
    if not segs:                      # 舊版 chunks.json 無偏移可據，退回子串測試
        return (None, None) if ev in c.get("chunk", "") else None
    for s in segs:
        i = s["text"].find(ev)
        if i >= 0:
            return (s["start"] + i, s["start"] + i + len(ev))
    return None


def strict_check(r):
    """從語料重新讀入該卷，按地址切片逐字比對。

    回傳 True／False；缺語料或無偏移（舊資料）則 None，表示無從核對。
    """
    sp, a, b = r.get("src"), r.get("ev_start"), r.get("ev_end")
    if not sp or a is None:
        return None
    p = pathlib.Path(sp)
    if not p.exists():
        return None
    return norm_text(p)[a:b] == r["evidence"]


# ── 人名歸一：只提候選，不自行合併 ────────────────────────
def alias_candidates(rows):
    """提出人名歸一候選。

    抽取規格要求 person 照原文寫法，不補姓氏，於是《宋書》的「善明」與
    《魏書》的「劉善明」會各自成條：跨立場比對因此落空，兩邊還都被記成
    「單方獨載」。這裡用確定性規則提出候選 —— 短名是長名的真後綴，
    且長名多出 1–3 字（姓氏的長度）。併與不併由人裁定。
    """
    forms = {}
    for r in rows:
        forms.setdefault(r["person"], []).append(r)
    names = sorted(forms)

    def where(n):
        return sorted({f"{x['book']}·{x['juan']}" for x in forms[n]})

    out = []
    for short in names:
        if len(short) < 2:
            continue
        longs = [n for n in names if n != short and n.endswith(short)
                 and 1 <= len(n) - len(short) <= 3]
        if not longs:
            continue
        shared = [n for n in longs if set(where(n)) & set(where(short))]
        out.append({
            "short": short,
            "candidates": longs,
            "suggest": longs[0] if len(longs) == 1 else None,
            "ambiguous": len(longs) > 1,
            "short_seen_in": where(short),
            "candidate_seen_in": {n: where(n) for n in longs},
            "same_juan_with": shared,
            "why": ("短名為長名之真後綴，差 "
                    + "／".join(str(len(n) - len(short)) for n in longs)
                    + " 字（疑為姓氏）"
                    + ("；且同卷共現，益可信" if shared else "")
                    + ("；對上多個長名，須人工裁定" if len(longs) > 1 else "")),
        })
    return out


def apply_aliases(rows, amap):
    """amap 形如 {"善明": "劉善明"}。原寫法留在 person_raw，不丟失。"""
    n = 0
    for r in rows:
        tgt = amap.get(r["person"])
        if tgt and tgt != r["person"]:
            r["person_raw"] = r["person"]
            r["person"] = tgt
            n += 1
    return n


# ── 確定性層：矛盾 / 歧異 / 獨載 ──────────────────────────
def analyse(rows, all_stances, baseline=None):
    people = {}
    for r in rows:
        people.setdefault(r["person"], []).append(r)

    out = []
    for name, hits in people.items():
        stances = sorted({h["stance"] for h in hits})
        acts = sorted({a for h in hits for a in h.get("acts", [])})
        flags = []

        term = [a for a in acts if a in TERMINAL]
        if len(term) > 1:
            flags.append((5, f"終局互斥：{' ↔ '.join(term)}",
                          "同一人被記為兩種不相容的結局，必有一方曲筆。"))
        for x, y in PAIRS:
            if x in acts and y in acts:
                flags.append((4, f"記載互斥：{x} ↔ {y}", "兩處斷言不能同真。"))

        by_act = {}
        for h in hits:
            if h.get("time"):
                for a in h.get("acts", []):
                    by_act.setdefault(a, set()).add(h["time"])
        for a, ts in by_act.items():
            if len(ts) > 1:
                flags.append((4, f"紀年歧異（{a}）：{' / '.join(sorted(ts))}",
                              "同一事繫於不同時間，考異之常見入口。"))

        # 終局獨載：兩系都記其人，卻只有一系記其死 —— 最強的諱飾信號。
        # 但「不記其死」要能當證據，先得比對該書記同類人之死的正常比率。
        if len(stances) > 1 and term:
            for a in term:
                who = sorted({h["stance"] for h in hits
                              if a in h.get("acts", [])})
                silent = [s for s in stances if s not in who]
                if silent:
                    wt = 5
                    why = (f"「{'、'.join(silent)}」記其人而不記其終。"
                           f"一方詳其死、另一方諱其死，曲筆之典型。")
                    sb = sorted({h["book"] for h in hits
                                 if h.get("stance") in silent})
                    rs = [(b, baseline[b]["rate"], baseline[b]["n"])
                          for b in sb
                          if baseline and b in baseline
                          and baseline[b].get("rate") is not None]
                    if rs:
                        bk, rate, n = max(rs, key=lambda x: x[1])
                        why += f"\n      基線：《{bk}》對同類人物的終局著錄率 " \
                               f"{rate:.0%}（n={n}）。"
                        if rate < 0.3:
                            wt = 2
                            why += "該書本就少記終局，沉默不足為奇，權重已下調。"
                        elif rate >= 0.7:
                            why += "該書通常記終局，此處沉默確屬異常。"
                    else:
                        why += ("\n      無基線可比 —— 跑 `huijian.py baseline` "
                                "算出該書的正常著錄率再判斷。")
                    flags.append((wt, f"終局獨載（{a}）：僅「{'、'.join(who)}」有載",
                                  why))

        missing = [s for s in all_stances if s not in stances]
        if len(all_stances) > 1 and missing:
            flags.append((3, f"僅見於「{'、'.join(stances)}」",
                          f"「{'、'.join(missing)}」無載。可能是諱飾，也可能該書本不記此類事，"
                          f"或材料散佚。需比對同類人物的正常著錄率再判斷"
                          f"（`huijian.py baseline` 可算，但目前只算終局著錄率，"
                          f"不算「是否提及」的著錄率）。"))

        if len(hits) > 1 and not flags:
            flags.append((1, "多處互見，無衝突", "記載彼此一致，可作交叉佐證。"))

        out.append({"person": name, "stances": stances, "acts": acts,
                    "flags": flags, "hits": hits,
                    "score": sum(w for w, _, _ in flags)})
    return sorted(out, key=lambda x: -x["score"])


def report(res, dropped, fh=sys.stdout, note=""):
    print(f"\n{'='*64}\n人物 {len(res)}　攔下偽引 {dropped} 條\n{'='*64}", file=fh)
    if note:
        print(f"\n⚠ {note}\n", file=fh)
    for p in res:
        print(f"\n■ {p['person']}　[{'|'.join(p['stances'])}]　權重 {p['score']}", file=fh)
        for e in p.get("known") or []:
            src = e.get("source") or "未註明出處"
            tp = f"（{e['topic']}）" if e.get("topic") else ""
            print(f"  ◆ 考異已及{tp}：{src}", file=fh)
        for w, k, why in p["flags"]:
            mark = "⚠" if w >= 4 else "·"
            print(f"  {mark} {k}\n      {why}", file=fh)
        for h in p["hits"]:
            meta = " ".join(filter(None, [h.get("title"), h.get("time")])) or "—"
            print(f"    《{h['book']}·{h['juan']}》{meta}", file=fh)
            print(f"      「{h['evidence']}」", file=fh)


def cmd_run(a):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and not a.dry_run:
        sys.exit("需要環境變數 ANTHROPIC_API_KEY（或加 --dry-run 離線試跑）")
    terms = variants(a.term, a.also or ())
    print(f"檢索詞: {'、'.join(terms)}", file=sys.stderr)
    hits = scan(terms, a.window)
    print(f"命中 {len(hits)} 段，開始抽取…", file=sys.stderr)
    rows, dropped = extract(hits, key, a.limit, a.dry_run, a.debug)
    if a.dry_run:
        print(f"\n--dry-run：本次會發出上列請求，未實際呼叫 API。", file=sys.stderr)
        return
    res = analyse(rows, sorted({h.stance for h in hits}))
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    json.dump(res, (out / f"{a.term}_findings.json").open("w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with (out / f"{a.term}_report.txt").open("w", encoding="utf-8") as fh:
        report(res, dropped, fh)
    report(res, dropped)
    print(f"\n→ {out}/{a.term}_findings.json（可導入 React demo 人工核定）", file=sys.stderr)



# ══ Agent 模式：不需要 API key，由 Claude Code 充當 L2 抽取層 ══

def _merge(spans):
    """把同卷中相互重疊或緊鄰的窗口併成最大連續段，接縫因此減到最少。"""
    out = []
    for start, text in sorted(spans):
        if out:
            ps, pt = out[-1]
            pend = ps + len(pt)
            if start <= pend:                        # 重疊或緊接
                if start + len(text) > pend:         # 向後延伸
                    out[-1] = (ps, pt + text[pend - start:])
                continue
        out.append((start, text))
    return out


def _split(start, text, n):
    """過長的連續段切小。chunks() 只切不改，故偏移可以累加。"""
    out, off = [], 0
    for piece in chunks(text, n):
        out.append((start + off, piece))
        off += len(piece)
    return out


def _mkchunks(hits, n=1400):
    """同卷窗口併段、切塊，每塊記下各段在原卷中的絕對偏移。

    一塊可能含數個不相鄰的段，接縫以 SEAM 標出；校驗時要求引文落在單一
    段內，模型便無法靠跨縫拼接造出「原文有」的假引文。
    """
    groups = {}
    for h in hits:
        groups.setdefault((h.book, h.juan, h.stance, h.src), []).append(
            (h.start, h.ctx))
    out = []
    for (bk, juan, st, srcpath), spans in groups.items():
        pieces = []
        for start, text in _merge(spans):
            pieces += _split(start, text, n)
        buf = []

        def flush():
            if not buf:
                return
            out.append({"ci": len(out), "book": bk, "juan": juan, "stance": st,
                        "src": srcpath,
                        "segments": [{"start": s, "text": t} for s, t in buf],
                        "chunk": SEAM.join(t for _, t in buf)})
            buf.clear()

        for start, text in pieces:
            if buf and sum(len(t) for _, t in buf) + len(text) > n:
                flush()
            buf.append((start, text))
        flush()
    return out


def _dump(path, obj):
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def cmd_chunks(a):
    """階段一：主題詞檢索 → 待抽取 chunk"""
    terms = variants(a.term, a.also or ())
    hits = scan(terms, a.window)
    cks = _mkchunks(hits)
    w = pathlib.Path(a.out)
    _dump(w / "chunks.json", cks)
    _dump(w / "meta.json", {"term": a.term, "terms": terms, "stage": 1})
    print(f"檢索詞 {'、'.join(terms)}")
    print(f"命中 {len(hits)} 段 → {len(cks)} 個 chunk → {w}/chunks.json")
    print(f"\n下一步：讀 {w}/chunks.json，對每個 chunk 做抽取，")
    print(f"       結果寫入 {w}/extracted.json（格式見 README）")


def cmd_expand(a):
    """階段二：用階段一抽出的人名，全語料重新檢索

    這一步不可省。以地名檢索時，同一人不會在兩方史書都命中，
    獨載檢測會對所有人觸發，全無意義。
    """
    w = pathlib.Path(a.out)
    items = json.load(open(w / "extracted.json", encoding="utf-8"))
    names = sorted({i["person"] for i in items if i.get("person")})
    if not names:
        sys.exit("extracted.json 裡沒有人名")
    terms = []
    for n in names:
        terms += variants(n)
    hits = scan(sorted(set(terms)), a.window)
    cks = _mkchunks(hits)
    _dump(w / "chunks2.json", cks)
    _dump(w / "meta.json", {"stage": 2, "names": names})
    print(f"階段一得到 {len(names)} 人：{'、'.join(names[:12])}{'…' if len(names) > 12 else ''}")
    print(f"人名全庫重檢索 → {len(hits)} 段 → {len(cks)} 個 chunk → {w}/chunks2.json")
    print(f"\n下一步：抽取後寫入 {w}/extracted2.json，再跑 verify")


def cmd_verify(a):
    """校驗 + 分析。不呼叫任何 API。"""
    w = pathlib.Path(a.out)
    rows, dropped = [], []
    for ckf, exf in (("chunks.json", "extracted.json"),
                     ("chunks2.json", "extracted2.json")):
        if not (w / ckf).exists() or not (w / exf).exists():
            continue
        cks = {c["ci"]: c for c in json.load(open(w / ckf, encoding="utf-8"))}
        for it in json.load(open(w / exf, encoding="utf-8")):
            c = cks.get(it.get("ci"))
            if c is None:
                dropped.append({**it, "why": "ci 不存在"})
                continue
            ev = (it.get("evidence") or "").strip()
            loc = locate(ev, c)
            if loc is None:
                why = ("引文跨越中略，疑為拼接"
                       if len(ev) >= 4 and ev in c.get("chunk", "")
                       else "引文不在原文")
                dropped.append({**it, "why": why,
                                "chunk": c.get("chunk", ""),
                                "book": c.get("book"), "juan": c.get("juan")})
                continue
            rows.append({**it, "evidence": ev, "book": c["book"],
                         "juan": c["juan"], "stance": c["stance"],
                         "src": c.get("src"),
                         "ev_start": loc[0], "ev_end": loc[1]})
    rows = [r for r in rows if r.get("person")]
    if not rows:
        sys.exit("無有效抽取結果")

    if getattr(a, "strict", False):
        kept, bad, skip = [], 0, 0
        for r in rows:
            ok = strict_check(r)
            if ok is None:
                skip += 1
                kept.append(r)
            elif ok:
                kept.append(r)
            else:
                bad += 1
                dropped.append({**r, "why": "按地址重新切片不符"})
        rows = kept
        print(f"--strict：重新切片核對 —— 不符 {bad} 條，無從核對 {skip} 條",
              file=sys.stderr)

    seen, uniq = set(), []
    for r in rows:                       # 兩階段會重複抽到同一段
        k = (r["person"], r["evidence"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)

    # ── 人名歸一 ──
    amap, note = {}, ""
    af = w / "aliases.json"
    if af.exists():
        amap = json.load(open(af, encoding="utf-8"))
        print(f"人名歸一：{len(amap)} 條別名，改寫 {apply_aliases(uniq, amap)} 條記載",
              file=sys.stderr)
    cands = [c for c in alias_candidates(uniq) if c["short"] not in amap]
    if cands:
        _dump(w / "aliases_suggested.json", cands)
        lst = "；".join(f"{c['short']}→{c['suggest'] or '？'}" for c in cands[:6])
        note = (f"人名歸一未完成：{len(cands)} 組候選待裁定（{lst}"
                f"{'…' if len(cands) > 6 else ''}）。\n"
                f"  未歸一時同一人的不同寫法各自成條，跨立場比對落空，"
                f"兩邊還都會被記成「單方獨載」。\n"
                f"  裁定：編輯 {w}/aliases_suggested.json，留下確認的對應，"
                f'存成 {w}/aliases.json（格式 {{"善明": "劉善明"}}），再跑一次 verify。')

    base = None
    bf = w / "baseline.json"
    if bf.exists():
        base = json.load(open(bf, encoding="utf-8"))
        print(f"基線：讀入 {len(base)} 部書的終局著錄率", file=sys.stderr)
    else:
        print("提示：尚無基線。跑 `python huijian.py baseline "
              f"{w}` 可校準終局獨載的權重", file=sys.stderr)
    res = analyse(uniq, sorted({r["stance"] for r in uniq}), base)

    zy = load_zhiyi(w)
    if zy is None:
        nov = ("新穎性未知：未載入知異索引，系統分不清哪條是新發現、"
               "哪條是前人早已論及。\n"
               "  **在建起這層掩碼之前，不要據本報告聲稱任何「新發現」。**\n"
               "  辦法見 docs/06「沒有『已知集合』」一節。")
        print("\n⚠ " + nov, file=sys.stderr)
        note = (note + "\n\n" if note else "") + nov
    else:
        k = mark_known(res, zy)
        print(f"知異索引：{len(zy)} 條，命中 {k} 人（報告中以 ◆ 標出）",
              file=sys.stderr)
        if k == 0:
            note = ((note + "\n\n" if note else "")
                    + f"知異索引 {len(zy)} 條，本次無一命中 —— "
                      f"或確屬未論及，或索引覆蓋不足，兩者無法由此分辨。")
    _dump(w / "findings.json", res)
    # 攔截紀錄要存下來：攔截率是模型質量的在線指標，但「攔得對不對」
    # 本身沒被量過。存檔才能當負對照標注（biaozhu.py 的 C 軌）。
    _dump(w / "dropped.json", dropped)
    if dropped:
        print(f"攔下 {len(dropped)} 條偽引：", file=sys.stderr)
        for d in dropped[:10]:
            print(f"  ✗ {d.get('person','?')}「{(d.get('evidence') or '')[:26]}」 {d['why']}",
                  file=sys.stderr)
    with (w / "report.txt").open("w", encoding="utf-8") as fh:
        report(res, len(dropped), fh, note)
    report(res, len(dropped), note=note)
    if note:
        print("\n⚠ " + note, file=sys.stderr)
    print(f"\n→ {w}/findings.json　{w}/report.txt", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="互見 — 二十四史交叉比對")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="下載並轉換語料").set_defaults(fn=cmd_fetch)
    for name, fn, extra in [("search", cmd_search, False),
                            ("dossier", cmd_dossier, True),
                            ("chunks", cmd_chunks, True),
                            ("run", cmd_run, True)]:
        p = sub.add_parser(name)
        p.add_argument("term")
        p.add_argument("--also", nargs="*", help="額外別名，如 田横岛 环州")
        p.add_argument("--window", type=int, default=200)
        if extra:
            p.add_argument("-o", "--out", default="out")
        if name == "run":
            p.add_argument("--limit", type=int, default=0, help="最多呼叫幾次 API（控成本）")
            p.add_argument("--dry-run", action="store_true", help="只列出將發出的請求")
            p.add_argument("--debug", action="store_true", help="逐條列印模型返回與攔截")
        p.set_defaults(fn=fn)
    for nm, fn in [("expand", cmd_expand), ("verify", cmd_verify),
                   ("baseline", cmd_baseline), ("null", cmd_null)]:
        q = sub.add_parser(nm)
        q.add_argument("out", help="工作目錄")
        q.add_argument("--window", type=int, default=200)
        if nm == "null":
            q.add_argument("--iters", type=int, default=500,
                           help="置換次數，預設 500")
            q.add_argument("--seed", type=int, default=0, help="隨機種子，便於復現")
        if nm == "verify":
            q.add_argument("--strict", action="store_true",
                           help="按地址從語料重新切片逐字核對（需 corpus/ 在位）")
        q.set_defaults(fn=fn)

    a = ap.parse_args()
    if a.cmd not in ("fetch", "verify", "null") and not COR.exists():
        sys.exit("先跑 python huijian.py fetch")
    a.fn(a)


if __name__ == "__main__":
    main()
