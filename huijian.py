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

# ── 各書的紀事起訖（公元）────────────────────────────────
# 兩個用處：年號消歧（《泰始二年》在《晉書》是 266、在《宋書》是 466）,
# 以及判斷一部書的沉默有沒有意義 —— 《三國志》不記張稷不是諱飾。
BOOK_SPAN = {
    "史记": (-2100, -90), "汉书": (-206, 23), "后汉书": (25, 220),
    "三国志": (184, 280), "晋书": (220, 420),
    "宋书": (420, 479), "南齐书": (479, 502), "梁书": (502, 557),
    "陈书": (557, 589), "南史": (420, 589),
    "魏书": (386, 550), "北齐书": (534, 577), "周书": (535, 581),
    "北史": (386, 618), "隋书": (581, 618),
    "旧唐书": (618, 907), "新唐书": (618, 907),
    "旧五代史": (907, 960), "新五代史": (907, 960),
    "宋史": (960, 1279), "辽史": (907, 1125), "金史": (1115, 1234),
    "元史": (1206, 1368), "明史": (1368, 1644),
}


# ── 源流關係：哪部書是哪部書的刪削派生本 ──────────────────────
# 為什麼非得標出來：立場軸上的每一個信號都預設各書互為獨立證人,
# 而派生本與其史源**逐字雷同是常態**。兩者一致不構成互證（那只是抄錄）,
# 兩者不一致才有意義（那是改筆）。把源流對當成跨立場互證,
# 等於把「抄得很像」讀成「兩方都這麼說」。
#
# 實測：郁洲案例的 22 組事件對照裡 12 組是源流對，而權重最高的一組是
# 《梁書》「以吳興太守張稷为尚書左僕射」對《南史》同句作「爲」——
# 差別只有一個異體字，卻拿到權重 9。
DERIVED_FROM = {
    "南史": {"宋书", "南齐书", "梁书", "陈书"},
    "北史": {"魏书", "北齐书", "周书", "隋书"},
    "新唐书": {"旧唐书"},
    "新五代史": {"旧五代史"},
}


def derivation(a, b):
    """兩書是否為源流關係。回傳 (派生本, 史源)，否則 None。"""
    if b in DERIVED_FROM.get(a, ()):
        return a, b
    if a in DERIVED_FROM.get(b, ()):
        return b, a
    return None


def independent_witnesses(books):
    """去掉「史源也在場」的派生本，回傳 (獨立史源, [(派生本, 理由)])。

    派生本只在其史源缺席時才算一個獨立證人 —— 此時它所存的可能正是
    已散佚的史源文字（《宋書》《南齊書》缺列傳，《南史》往往是唯一
    所存，見 README「語料」一節）。史源在場時，它不添任何獨立性。
    """
    books = set(books)
    keep, drop = set(), []
    for b in sorted(books):
        src = sorted(DERIVED_FROM.get(b, set()) & books)
        if src:
            drop.append((b, "刪削自《" + "》《".join(src) + "》，與史源同在，不另計"))
        else:
            keep.add(b)
    return keep, drop


# ── 行為類型與互斥規則（確定性層）────────────────────────────
ACTS = ["除授", "罢黜", "赴任", "征战", "战胜", "战败", "死于战", "病卒",
        "被杀", "归降", "被俘", "叛乱", "筑城", "赈济", "上书", "出使",
        "逃亡", "受封赏"]
TERMINAL = {"死于战", "病卒", "被杀", "归降", "被俘"}   # 任兩者並存＝硬矛盾
PAIRS = [("战胜", "战败")]

# 故意留的錯誤規則。一個人先任後免是正常序列，這條必然誤報。
# 留著是為了讓人第一次跑就明白規則需要校準 —— 但**預設不啟用**：
# 預設就誤報的工具沒法用，而且公開倉庫裡沒人知道哪條是故意的。
# 要親眼看它誤報：verify / run 加 --demo-bad-rule。
DEMO_BAD_PAIRS = [("除授", "罢黜")]

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
# 現代白話的判別：文言幾乎不用結構助詞「的」。實測全語料 2,413 卷,
# 「的」密度中位數 0.00／千字、p99 只有 0.59，而上游那一卷通篇白話的
# 檔案是 16.00 —— 差兩個數量級，門檻放在 2.0 仍留三倍餘裕。
VERNACULAR_MAX = 2.0

# 譯文段落的界線寫作「译文（人名、人名…）」或「译文：」。
# **不可**把孤零零一行「译文」也當界線 —— 那是頁首的導覽標籤
# （《史記》各頁作「段译 / 译文」兩個連結），出現在原文之前；
# 當成界線會把整卷原文切掉。初版就是這麼錯的，三國志、史記、
# 漢書 三部書當場歸零。
VERNACULAR_RE = re.compile(r"^(译文|譯文)\s*[（(：:]")
NAV_TABS = ("译文", "譯文", "段译", "段譯")


def vernacular_density(t):
    """每千字的「的」字數。粗，但在這份語料上區分力極強。"""
    return t.count("的") / len(t) * 1000 if t else 0.0


def html_to_text(p):
    s = p.read_text(encoding="utf-8", errors="ignore")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = html.unescape(s)
    junk = ("←", "首页", "目录", "下一节", "上一节", "原文", "返回", "阿星星",
            "上一篇目录下一篇")
    out = []
    for l in (x.strip() for x in s.split("\n")):
        if not l or any(l.startswith(j) for j in junk):
            continue
        # 上游有些頁面在原文之後直接接上現代白話譯文，中間只有一行
        # 「译文（……）」作界，而檔名仍叫「原文」—— 靠檔名排除不掉。
        # 這一段若混進語料，抽取會把譯者的話當成史源，而逐字校驗
        # **攔不住它**：譯文確實逐字存在於 chunk 裡。
        if l in NAV_TABS:
            continue                       # 導覽標籤，跳過這一行即可
        # 只有累積了足夠原文之後才認界線，免得頁首的任何東西
        # 把整卷切光 —— 寧可多留一段譯文，不可丟掉一整卷原文。
        if VERNACULAR_RE.match(l) and sum(len(x) for x in out) >= 200:
            break
        out.append(l)
    return "\n".join(out)


def cmd_fetch(_):
    if not RAW.exists():
        print("clone …", file=sys.stderr)
        subprocess.run(["git", "clone", "--depth", "1", "-q", REPO], check=True)
    COR.mkdir(exist_ok=True)
    stats, skipped = [], []
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
            # 另有整卷誤標的：檔名作「原文」，內容卻通篇白話，且沒有
            # 「译文」界線可依（實測上游 晋书／帝纪／第十章 即是）。
            # 這類只能靠內容判，判掉就報出來，不靜默丟棄。
            vd = vernacular_density(t)      # d 已是輸出目錄，勿覆蓋
            if vd > VERNACULAR_MAX:
                skipped.append((book.name, f.name, vd))
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
    if skipped:
        print(f"\n排除 {len(skipped)} 卷：內容為現代白話，檔名卻標作原文",
              file=sys.stderr)
        for bk, fn, d in skipped:
            print(f"  ✗ {bk}／{fn}　「的」密度 {d:.2f}／千字", file=sys.stderr)


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


def _pairs(demo):
    if not demo:
        return list(PAIRS)
    print("--demo-bad-rule：已啟用故意的錯誤規則 "
          f"{DEMO_BAD_PAIRS}，它會誤報，這是示範用的。", file=sys.stderr)
    return list(PAIRS) + list(DEMO_BAD_PAIRS)


# ── 基線：該書記同類人之死的正常比率 ──────────────────────
# 列傳開篇的定式「<名>字<某>，<籍貫>人也」是傳主姓名的現成來源。
# 精度不高（約六到八成，且各書不均 —— 《魏書》多作「某，字某」，
# 書志類還會把「一卷字」之類誤收），所以只拿它當**參照群體**，
# 不當人名權威表。雜訊會稀釋比率，方向上偏保守。
SUBJECT_RE = re.compile(r"([\u4e00-\u9fff]{2,4})，?字[\u4e00-\u9fff]{1,2}，")
NOT_NAME = set("卷篇章一二三四五六七八九十百千第上下左右前後后書书年月日")
# 姓氏表。沒有它，正則會把「字」前兩三個字一律當人名，於是《魏書》
# 抓出「乃以墨涂」「之子磨奴」這類碎片 —— 雜訊名後面不會接「卒」,
# 比率被壓到 7%，看起來像該書諱言死亡，其實是抓錯了名字。
# 含北朝複姓，否則魏書、北史、周書的傳主會被整批漏掉。
SURNAMES = set(
    "趙赵錢钱孫孙李周吳吴鄭郑王馮冯陳陈褚衛卫蔣蒋沈韓韩楊杨朱秦尤許许何呂吕"
    "施張张孔曹嚴严華华金魏陶姜戚謝谢鄒邹喻柏水竇窦章雲云蘇苏潘葛奚范彭郎魯鲁"
    "韋韦昌馬马苗鳳凤花方俞任袁柳酆鮑鲍史唐費费廉岑薛雷賀贺倪湯汤滕殷羅罗畢毕"
    "郝鄔邬安常樂乐于時时傅皮齊齐康伍余元卜顧顾孟平黃黄和穆蕭萧尹姚邵湛汪祁毛"
    "禹狄米貝贝明臧計计伏成戴談谈宋茅龐庞熊紀纪舒屈項项祝董梁杜阮藍蓝閔闽席季"
    "麻強强賈贾路婁娄危江童顏颜郭梅盛林刁鍾钟徐邱駱骆高夏蔡田樊胡凌霍虞萬万支"
    "柯昝管盧卢莫經经房裘繆缪干解應应宗丁宣賁贲鄧邓郁單单杭洪包諸诸左石崔吉鈕"
    "龔龚程嵇邢滑裴陸陆榮荣翁荀羊惠甄曲家封芮羿儲储靳汲邴糜松井段富巫烏乌焦巴"
    "弓牧隗山谷車车侯宓蓬全郗班仰秋仲伊宮宫寧宁仇欒栾暴甘鈄厲厉戎祖武符劉刘景"
    "詹束龍龙葉叶幸司韶郜黎薊蓟薄印宿白懷怀蒲邰從从鄂索咸籍賴赖卓藺蔺屠蒙池喬"
    "喬乔陰阴胥能蒼苍雙双聞闻莘黨党翟譚谭貢贡勞劳逄姬申扶堵冉宰雍璩桑桂濮牛壽"
    "壽寿通邊边扈燕冀郟浦尚農农溫温別别莊庄晏柴瞿閻阎充慕連连茹習习宦艾魚鱼容"
    "向古易慎戈廖庾終终暨居衡步都耿滿满弘匡國国文寇廣广祿禄闕阙東东歐欧殳沃利"
    "蔚越夔隆師师鞏巩厙库聶聂晁勾敖融冷訾辛闞阚那簡简饒饶空曾毋沙乜養养鞠須须"
    "豐丰巢關关蒯相查后荊荆紅红游竺權权逯蓋盖益桓公万俟")
COMPOUND_SURNAMES = (
    "司馬", "司马", "慕容", "拓跋", "宇文", "長孫", "长孙", "獨孤", "独孤",
    "尉遲", "尉迟", "賀蘭", "贺兰", "歐陽", "欧阳", "上官", "諸葛", "诸葛",
    "夏侯", "皇甫", "公孫", "公孙", "赫連", "赫连", "万俟", "叱羅", "叱罗",
    "乙弗", "吐谷", "斛斯", "步六孤", "丘穆陵", "紇骨", "纥骨", "普六茹",
)


def _looks_like_name(nm):
    """捕到的字串像不像人名：以姓氏起頭。

    這是精度的主要來源。沒有它，harvest 的雜訊會系統性地壓低比率,
    而壓低的方向正好讓獨載的沉默顯得正常 —— 最危險的那個方向。
    """
    if any(nm.startswith(c) for c in COMPOUND_SURNAMES):
        return len(nm) >= 3
    return nm[0] in SURNAMES
# 樣本太小的比率不可用來調權重。n 低於此數即視同「無基線」。
BASELINE_MIN_N = 20


def reference_names(limit_per_book=400):
    """各書的傳主姓名，作為基線的參照群體。

    不用當次檢索結果當群體 —— 那只有十來個人，每部書的 n 落到個位數，
    比率毫無意義（實測 n=1 時報 100%）。參照群體要大到比率站得住。
    """
    per = {}
    for f in sorted(COR.rglob("*.txt")):
        if any(w in f.name for w in ("译文", "譯文", "白话", "白話", "段译")):
            continue
        s = norm_text(f)
        bag = per.setdefault(f.parts[1], set())
        for m in SUBJECT_RE.finditer(s):
            nm = m.group(1)
            if len(nm) < 2 or any(c in NOT_NAME for c in nm):
                continue
            if not _looks_like_name(nm):
                continue
            bag.add(nm)
    return {b: sorted(v)[:limit_per_book] for b, v in per.items()}

SENT_END = "。！？；"


def _attributable(s, nm, m, window, others=()):
    """名字之後、同一句之內，是否緊接著終局用語。

    初版取前後各 120 字的窗口，只要窗內出現終局用語就算。在真語料上
    這完全不成立：「卒」在列傳裡俯拾即是（每篇傳記都以某人卒收尾），
    於是任何在列傳中出現過的名字都會被算成「記其終」—— 實測三國志、
    史記、後漢書全部報 100%。基線一旦普遍偏高，所有沉默都顯得正常，
    獨載信號被整體抹平，比沒有基線更糟。

    改成：終局用語必須出現在人名**之後**、且與人名**同句**（中間沒有
    句讀），距離不超過 window 字。文言的死亡記述幾乎都是「某卒」
    「某見殺」這樣緊接的寫法，所以這個條件嚴而不失。
    """
    seg = s[m.end():min(len(s), m.end() + window)]
    cut = min([seg.find(c) for c in SENT_END if c in seg] or [len(seg)])
    seg = seg[:cut]
    if any(o in seg for o in others):        # 同句裡還有別人，歸屬不明
        return False
    return any(w in seg for w in TERMINAL_LEX)


def terminal_rate(names, window=25):
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
    per_book = names if isinstance(names, dict) else None
    flat = None if per_book else [n for n in names
                                  if len(n) >= 2 and n not in GENERIC_NAMES]
    stat = {}
    for f in sorted(COR.rglob("*.txt")):
        if any(w in f.name for w in ("译文", "譯文", "白话", "白話", "段译")):
            continue
        book = f.parts[1]
        pool = per_book.get(book, []) if per_book else flat
        if not pool:
            continue
        s = norm_text(f)
        for nm in pool:
            if nm not in s:
                continue
            d = stat.setdefault(book, {"seen": set(), "term": set()})
            d["seen"].add(nm)
            # others 不可省：同句裡若還有別人，那句的「見殺」歸誰並不確定
            others = [o for o in pool if o != nm and o not in nm]
            for m in re.finditer(re.escape(nm), s):
                if _attributable(s, nm, m, window, others):
                    d["term"].add(nm)
                    break
    out = {}
    for book, d in stat.items():
        n, k = len(d["seen"]), len(d["term"])
        out[book] = {"n": n, "k": k, "rate": (k / n) if n else None,
                     "usable": n >= BASELINE_MIN_N}
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
    if a.cohort == "findings":
        names = _names_from_work(w)
        if not names:
            sys.exit(f"{w} 裡找不到人名（需先跑 verify 或抽取）")
        print(f"參照群體＝本次檢索結果，{len(names)} 人。", file=sys.stderr)
        print("  注意：這樣每部書的 n 只有個位數，比率站不住；"
              "正式用請改 --cohort reference。", file=sys.stderr)
    else:
        names = reference_names(a.limit)
        tot = len({n for v in names.values() for n in v})
        print(f"參照群體＝各書傳主姓名，共 {tot:,} 人（每書上限 {a.limit}）。",
              file=sys.stderr)
        print("  來源是列傳開篇定式「某字某，某地人也」，精度約六到八成；"
              "雜訊會稀釋比率，方向偏保守。", file=sys.stderr)
    base = terminal_rate(names, a.window)
    _dump(w / "baseline.json", base)
    print(f"{'書':10s} {'提及':>5s} {'記其終':>6s} {'著錄率':>7s}  可用")
    for b, d in sorted(base.items(), key=lambda x: -(x[1]["rate"] or 0)):
        print(f"{b:10s} {d['n']:5d} {d['k']:6d} {d['rate']:7.0%}"
              f"  {'是' if d['usable'] else f'否（n<{BASELINE_MIN_N}）'}")
    bad = [b for b, d in base.items() if not d["usable"]]
    if bad:
        print(f"\n{len(bad)} 部書的樣本不足（n<{BASELINE_MIN_N}），"
              f"其比率不會用來調權重：{'、'.join(bad)}", file=sys.stderr)
    print(f"\n→ {w}/baseline.json", file=sys.stderr)


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


def _relevant_stances(books, all_stances):
    """哪些立場的書，其紀事年代與現有記載重疊 —— 只有這些書的沉默有意義。

    《三國志》不記張稷（卒 513）不是諱飾，它記的是三國。舊版把語料裡
    出現過的每一個立場都算進「無載」，於是「僅見於」幾乎必然觸發 ——
    **這正是它通不過置換檢驗的機制**：打亂立場標籤不影響各書的斷代,
    所以旗標數量一點不降。它量到的是書的起訖，不是立場。
    """
    spans = [BOOK_SPAN[b] for b in books if b in BOOK_SPAN]
    if not spans:
        return list(all_stances)
    lo, hi = min(x[0] for x in spans), max(x[1] for x in spans)
    out = []
    for s in all_stances:
        for b, st in STANCE.items():
            if st != s or b not in BOOK_SPAN:
                continue
            blo, bhi = BOOK_SPAN[b]
            if blo <= hi and lo <= bhi:          # 年代重疊
                out.append(s)
                break
    return out


def _distinct_times(times):
    """把彼此相容的紀年寫法收成一個。

    檢索窗口只有兩百字，年號常落在窗外：《魏書》帝紀作「二年二月」,
    列傳作「延昌二年二月」，其實同一天。一方是另一方的後綴時，
    那是上下文缺失，不是歧異 —— 舊規則把它報成強信號。
    """
    out = []
    for t in sorted(times, key=len, reverse=True):
        if not any(t in kept for kept in out):
            out.append(t)
    return out


# ── 確定性層：矛盾 / 歧異 / 獨載 ──────────────────────────
def analyse(rows, all_stances, baseline=None, pairs=None):
    people = {}
    for r in rows:
        people.setdefault(r["person"], []).append(r)

    out = []
    for name, hits in people.items():
        stances = sorted({h["stance"] for h in hits})
        acts = sorted({a for h in hits for a in h.get("acts", [])})
        # 立場軸上的信號都預設各書互相獨立，派生本會虛增這個數。
        ind, derived = independent_witnesses({h["book"] for h in hits})
        flags = []

        term = [a for a in acts if a in TERMINAL]
        if len(term) > 1:
            flags.append((5, f"終局互斥：{' ↔ '.join(term)}",
                          "同一人被記為兩種不相容的結局，必有一方曲筆。"))
        for x, y in (PAIRS if pairs is None else pairs):
            if x in acts and y in acts:
                flags.append((4, f"記載互斥：{x} ↔ {y}", "兩處斷言不能同真。"))

        # 紀年歧異只對「一生只能發生一次」的行為成立。
        # 實測教訓：張稷在《梁書》本紀裡五度除授，舊規則把四個不同的
        # 任命當成「同一事繫於不同時間」，報了一條假歧異。除授、征战、
        # 赴任這類可以反覆發生，時間不同本是常態。
        by_act = {}
        for h in hits:
            if h.get("time"):
                for a in h.get("acts", []):
                    if a in TERMINAL:                 # 終局只能有一次
                        by_act.setdefault(a, set()).add(h["time"])
        for a, ts in by_act.items():
            ts = _distinct_times(ts)
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
                    wb = sorted({h["book"] for h in hits
                                 if a in h.get("acts", [])})
                    sb = sorted({h["book"] for h in hits
                                 if h.get("stance") in silent})
                    # 有載的一方若全是沉默那一方的派生本，這不是兩系相左,
                    # 而是同一脈之內的增補或脫文 —— 派生本不是獨立的
                    # 第二個證人，它的沉默與其史源的沉默不互相印證。
                    srcs = sorted({s for x in wb
                                   for s in DERIVED_FROM.get(x, ()) if s in sb})
                    same_lineage = bool(srcs) and all(
                        DERIVED_FROM.get(x, set()) & set(sb) for x in wb)
                    rs = [(b, baseline[b]["rate"], baseline[b]["n"])
                          for b in sb
                          if baseline and b in baseline
                          and baseline[b].get("rate") is not None
                          # 樣本不足的比率不可用來調權重：0/3 與 0/300
                          # 在數字上都是 0%，證據力卻差了兩個數量級。
                          and baseline[b].get("n", 0) >= BASELINE_MIN_N]
                    if same_lineage:
                        wt = 2
                        why = (f"《{'》《'.join(wb)}》記其終，而其史源"
                               f"《{'》《'.join(srcs)}》不記 —— 前者係刪削"
                               f"後者而成，同屬一脈，**不構成跨立場相左**。"
                               f"這是派生本的增補或史源的脫文，權重已下調。")
                    elif rs:
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

        # 只拿斷代重疊的書來問「為什麼它不記」。
        rel = _relevant_stances({h["book"] for h in hits}, all_stances)
        offperiod = [s for s in all_stances if s not in rel]
        missing = [s for s in rel if s not in stances]
        if len(ind) <= 1 and len(hits) >= 1:
            # 只有一個獨立史源時，「僅見於某系」是同義反覆 —— 它必然成立,
            # 不含任何立場信息。這才是該旗標通不過置換檢驗的真正原因：
            # 實測本案例 24 人中 19 人只見於一部書，怎麼打亂立場標籤,
            # 「僅見於」都會觸發。把這種情形改報為孤證，權重 0,
            # 信號留給真正有兩個以上獨立史源的人物。
            msg = f"獨立史源只有《{'》《'.join(sorted(ind))}》"
            if derived:
                msg += ("，另有" + "；".join(f"《{b}》{r}" for b, r in derived)
                        + "（同脈之內的雷同是抄錄，不是第二個證人）")
            flags.append((0, "孤證：僅一個獨立史源",
                          msg + "。無從互見 —— 此時「僅見於某系」是同義反覆，"
                          "故不計為信號。本工具對這類人物幫不上忙，"
                          "只能告訴你去哪看。"))
        elif len(rel) > 1 and missing:
            # 權重 1，不是 3。置換檢驗（`huijian.py null`）實測把立場標籤
            # 在書之間打亂，本旗標的數量一點不降（實測 24 對虛無均值
            # 24.00，p=1.000）—— 它量到的是語料覆蓋稀疏，不是立場差異。
            # 一個通不過自己虛無檢驗的信號不該按證據計分。
            why = (f"「{'、'.join(missing)}」無載。**這不是立場證據** —— "
                   f"置換檢驗實測本旗標打亂立場標籤後數量不降（p≈1.0）,"
                   f"因為這批人多數只在一部書裡出現過。它量到的是語料覆蓋"
                   f"稀疏，不是諱飾。只能當「去這幾部書翻翻」的索引用。")
            if offperiod:
                why += (f"\n      斷代不重疊、未計入：{'、'.join(offperiod)}"
                        f"　（那些書記的不是這段時期，其沉默無意義）。")
            if derived:
                why += ("\n      源流："
                        + "；".join(f"《{b}》{r}" for b, r in derived)
                        + f"　獨立史源實為 {len(ind)} 部。")
            flags.append((1, f"僅見於「{'、'.join(stances)}」", why))

        if len(hits) > 1 and not flags:
            if len(ind) > 1:
                flags.append((1, "多處互見，無衝突",
                              "記載彼此一致，且出自互相獨立的史源，可作交叉佐證。"))
            else:
                # 派生本與其史源一致只說明抄錄過，不是兩個證人都這麼說。
                flags.append((0, "多處互見，但非獨立",
                              "記載彼此一致，但"
                              + "；".join(f"《{b}》{r}" for b, r in derived)
                              + "。同脈之內的雷同是抄錄，不構成交叉佐證。"))

        out.append({"person": name, "stances": stances, "acts": acts,
                    "flags": flags, "hits": hits,
                    "witnesses": sorted(ind), "derived": derived,
                    "score": sum(w for w, _, _ in flags)})
    return sorted(out, key=lambda x: -x["score"])


def report(res, dropped, fh=sys.stdout, note=""):
    print(f"\n{'='*64}\n人物 {len(res)}　攔下偽引 {dropped} 條\n{'='*64}", file=fh)
    if note:
        print(f"\n⚠ {note}\n", file=fh)
    for p in res:
        wits = p.get("witnesses")
        print(f"\n■ {p['person']}　[{'|'.join(p['stances'])}]　權重 {p['score']}"
              + (f"　獨立史源 {len(wits)}" if wits else ""), file=fh)
        for b, r in p.get("derived") or []:
            print(f"  ◇ 源流：《{b}》{r}", file=fh)
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
    res = analyse(rows, sorted({h.stance for h in hits}), None,
                  _pairs(getattr(a, "demo_bad_rule", False)))
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


# 帝號、廟號、泛稱：出現在每一卷裡，當人名檢索毫無分辨力。
GENERIC_NAMES = {
    "帝", "上", "王", "公", "侯", "君", "太祖", "高祖", "世祖", "太宗",
    "高宗", "世宗", "中宗", "肅宗", "肃宗", "顯祖", "显祖", "獻帝", "献帝",
    "太后", "皇后", "太子", "天子", "朕", "臣", "將軍", "将军", "刺史",
}
# 一個真實人物的名字，在二十四史全文裡出現上千次是不合理的（劉裕 210 次）。
# 超過這個數的多半是泛稱、官稱，或與常用詞撞字。
NAME_FREQ_MAX = 1000


def _usable_names(names):
    """挑出能用來做全庫檢索的人名，並說明被篩掉的理由。

    抽取規格刻意要求 person 照原文寫法、不補姓氏，於是必然出現
    「恩」「翼」「昶」這類單字名；而階段二要拿人名去掃全語料 ——
    「帝」在這份語料裡出現 59,084 次，拿它當人名檢索只會灌出幾千個
    無意義的 chunk，把真正的比對埋掉。所以這裡必須先篩。
    """
    texts = None
    keep, drop = [], []
    for n in names:
        if len(n) < 2:
            drop.append((n, "單字名，無分辨力"))
            continue
        if n in GENERIC_NAMES:
            drop.append((n, "帝號或泛稱，非人名"))
            continue
        if texts is None:                  # 只在真要數的時候才讀全語料
            texts = [f.read_text(encoding="utf-8")
                     for f in sorted(COR.rglob("*.txt"))]
        c = sum(t.count(n) for t in texts)
        if c > NAME_FREQ_MAX:
            drop.append((n, f"全語料出現 {c:,} 次，過於常見"))
        else:
            keep.append(n)
    return keep, drop


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
    names, dropped_names = _usable_names(names)
    if dropped_names:
        print(f"篩掉 {len(dropped_names)} 個不能用於全庫檢索的寫法：",
              file=sys.stderr)
        for n, why in dropped_names:
            print(f"  ✗ {n}　{why}", file=sys.stderr)
        print("  這些人要進階段二，得先在 aliases.json 裡把它們歸一到全名"
              "（如 恩→孫恩），或用 --also 直接給全名。", file=sys.stderr)
    if not names:
        sys.exit("篩完沒有可用人名。先做人名歸一，或用 chunks --also 給全名。")
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
    res = analyse(uniq, sorted({r["stance"] for r in uniq}), base,
                  _pairs(getattr(a, "demo_bad_rule", False)))

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
            p.add_argument("--demo-bad-rule", action="store_true",
                           help="啟用故意的錯誤規則，看它如何誤報")
        p.set_defaults(fn=fn)
    for nm, fn in [("expand", cmd_expand), ("verify", cmd_verify),
                   ("baseline", cmd_baseline), ("null", cmd_null)]:
        q = sub.add_parser(nm)
        q.add_argument("out", help="工作目錄")
        q.add_argument("--window", type=int, default=200)
        if nm == "baseline":
            q.add_argument("--cohort", choices=("reference", "findings"),
                           default="reference",
                           help="參照群體：各書傳主（預設）或本次檢索結果")
            q.add_argument("--limit", type=int, default=400,
                           help="每書取多少傳主作參照，預設 400")
        if nm == "null":
            q.add_argument("--iters", type=int, default=500,
                           help="置換次數，預設 500")
            q.add_argument("--seed", type=int, default=0, help="隨機種子，便於復現")
        if nm == "verify":
            q.add_argument("--demo-bad-rule", action="store_true",
                           help="啟用故意的錯誤規則，看它如何誤報")
            q.add_argument("--strict", action="store_true",
                           help="按地址從語料重新切片逐字核對（需 corpus/ 在位）")
        q.set_defaults(fn=fn)

    a = ap.parse_args()
    if a.cmd not in ("fetch", "verify", "null") and not COR.exists():
        sys.exit("先跑 python huijian.py fetch")
    a.fn(a)


if __name__ == "__main__":
    main()
