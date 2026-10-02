#!/usr/bin/env python3
"""
對照 duizhao — 事件層並置

把同一事件在不同立場史書中的敘述並排擺出，並標出分歧。
補 huijian.py 的人物層之不足：人物層靠姓名錨定，事件層沒有姓名可錨，
須靠「共同人物 ∧ 共同地點 ∧ 紀年重合」三重錨定。

    python duizhao.py eras 延昌二年 天监十二年     # 紀年換算
    python duizhao.py align findings.json          # 事件對照
"""

import argparse, itertools, json, re, sys

# Windows 預設以本地代碼頁寫 stdout，重定向到檔案時中文即 UnicodeEncodeError。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# ── 年號表 ────────────────────────────────────────────────
# (年號, 元年公元, 立場)。可續補；未收之年號換算返回 None，不猜。
ERAS = [
    # 劉宋
    ("永初", 420, "南"), ("景平", 423, "南"), ("元嘉", 424, "南"),
    ("孝建", 454, "南"), ("大明", 457, "南"), ("永光", 465, "南"),
    ("景和", 465, "南"), ("泰始", 465, "南"), ("泰豫", 472, "南"),
    ("元徽", 473, "南"), ("昇明", 477, "南"), ("升明", 477, "南"),
    # 南齊
    ("建元", 479, "南"), ("永明", 483, "南"), ("隆昌", 494, "南"),
    ("建武", 494, "南"), ("永泰", 498, "南"), ("永元", 499, "南"),
    ("中兴", 501, "南"), ("中興", 501, "南"),
    # 梁
    ("天监", 502, "南"), ("天監", 502, "南"), ("普通", 520, "南"),
    ("大通", 527, "南"), ("中大通", 529, "南"), ("大同", 535, "南"),
    ("中大同", 546, "南"), ("太清", 547, "南"),
    # 北魏
    ("太延", 435, "北"), ("太平真君", 440, "北"), ("正平", 451, "北"),
    ("兴安", 452, "北"), ("兴光", 454, "北"), ("太安", 455, "北"),
    ("和平", 460, "北"), ("天安", 466, "北"), ("皇兴", 467, "北"),
    ("皇興", 467, "北"), ("延兴", 471, "北"), ("延興", 471, "北"),
    ("承明", 476, "北"), ("太和", 477, "北"), ("景明", 500, "北"),
    ("正始", 504, "北"), ("永平", 508, "北"), ("延昌", 512, "北"),
    ("熙平", 516, "北"), ("神龟", 518, "北"), ("正光", 520, "北"),
    ("孝昌", 525, "北"), ("武泰", 528, "北"), ("建义", 528, "北"),
    ("永安", 528, "北"), ("普泰", 531, "北"), ("太昌", 532, "北"),
    ("永熙", 532, "北"),
]
# 同名年號：歷代重用，字面完全相同。只收一個元年會把另一朝的紀年
# 悄悄換算成相差一兩百年的公元數，而錯誤的年份還會在 pair_score 裡
# 加 3 分，憑空造出「同年」錨定。收齊各自元年，交給 BOOK_SPAN 消歧。
ERAS += [
    ("建元", -140, "西漢"), ("建元", 343, "東晉"),
    ("建武", 25, "東漢"), ("建武", 317, "東晉"),
    ("永平", 58, "東漢"),
    ("永元", 89, "東漢"),
    ("永初", 107, "東漢"),
    ("和平", 150, "東漢"),
    ("元嘉", 151, "東漢"),
    ("太和", 227, "曹魏"),
    ("正始", 240, "曹魏"),
    ("泰始", 265, "西晉"),
    ("永熙", 290, "西晉"),
    ("太安", 302, "西晉"),
    ("永安", 304, "西晉"),
]
ERAS.sort(key=lambda e: -len(e[0]))          # 長年號優先，免得「大通」吃掉「中大通」

# 各書記事大致起訖（公元），僅用於同名年號消歧。寬鬆取界，
# 只求排除百年量級的誤判，不作精確斷限。
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

CN = {"元": 1, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
      "七": 7, "八": 8, "九": 9, "十": 10}


def cn_num(s):
    if not s:
        return None
    if s in CN:
        return CN[s]
    if s.startswith("十"):
        return 10 + CN.get(s[1:], 0)
    if "十" in s:
        a, _, b = s.partition("十")
        return CN.get(a, 0) * 10 + CN.get(b, 0)
    return None


def era_candidates(text):
    """字面匹配到的全部候選 [(公元, 年號, 第幾年)]。
    同名年號歷代重用，故可能多於一個。ERAS 已按名長排序，長年號在前。"""
    if not text:
        return []
    out = []
    for era, y0, _ in ERAS:
        m = re.search(re.escape(era) + r"([元一二三四五六七八九十]+)年", text)
        if m:
            n = cn_num(m.group(1))
            if n:
                out.append((y0 + n - 1, era, n))
    return out


def to_year(text, book=None):
    """『延昌二年』→ (513, '延昌', 2)。

    同名年號（如《晉書》的泰始與《宋書》的泰始）靠來源書名消歧。
    未收、無書名可據、或消歧後仍不唯一者返回 None —— 不猜。
    """
    cands = era_candidates(text)
    if not cands:
        return None
    era = cands[0][1]                        # 取最長的那個年號名
    same = [c for c in cands if c[1] == era]
    if len(same) == 1:
        return same[0]
    lo, hi = BOOK_SPAN.get(book or "", (None, None))
    if lo is not None:
        fit = [c for c in same if lo <= c[0] <= hi]
        if len(fit) == 1:
            return fit[0]
    return None


# ── 立場用語：同一事，勝方與敗方措辭不同 ──────────────────
STANCE_LEX = {
    "歸附": ["内附", "內附", "归诚", "歸誠", "来降", "來降", "归化", "歸化",
             "款诚", "款誠", "内属", "內屬", "举州入国", "舉州入國"],
    "叛離": ["叛", "反", "谋反", "謀反", "背", "亡命"],
    "敵來": ["寇", "侵", "入寇", "犯"],
    "我往": ["讨", "討", "征", "伐", "平", "克"],
    "蔑稱": ["岛夷", "島夷", "索虏", "索虜", "僭", "伪", "偽", "贼", "賊"],
}
OPPOSED = [("歸附", "叛離"), ("敵來", "我往")]


def lex_tags(text):
    return {k for k, ws in STANCE_LEX.items() if any(w in text for w in ws)}


# ── 事件對齊 ──────────────────────────────────────────────
def anchors(row):
    return {
        "person": row.get("person") or "",
        "place": row.get("place") or "",
        "year": to_year(row.get("time", ""), row.get("book")),
        "acts": set(row.get("acts") or []),
        "stance": row.get("stance", "?"),
    }


def pair_score(a, b):
    """三重錨定。人物最重，紀年次之，地點再次。"""
    s, why = 0, []
    if a["person"] and a["person"] == b["person"]:
        s += 3
        why.append(f"同人物 {a['person']}")
    if a["place"] and a["place"] == b["place"]:
        s += 2
        why.append(f"同地點 {a['place']}")
    if a["year"] and b["year"]:
        d = abs(a["year"][0] - b["year"][0])
        if d == 0:
            s += 3
            why.append(f"同年 {a['year'][0]}")
        elif d == 1:
            s += 1
            why.append(f"相鄰年 {a['year'][0]}/{b['year'][0]}")
    if a["acts"] & b["acts"]:
        s += 1
        why.append("行為重疊 " + "、".join(sorted(a["acts"] & b["acts"])))
    return s, why


def diverge(ra, rb, aa, ab):
    """並置之後，分歧在哪。"""
    out = []
    if aa["year"] and ab["year"] and aa["year"][0] != ab["year"][0]:
        out.append((5, "紀年歧異",
                    f"{ra['book']}作{aa['year'][1]}{aa['year'][2]}年"
                    f"（{aa['year'][0]}）／{rb['book']}作{ab['year'][1]}{ab['year'][2]}年"
                    f"（{ab['year'][0]}）"))

    ta, tb = lex_tags(ra["evidence"]), lex_tags(rb["evidence"])
    for x, y in OPPOSED:
        if (x in ta and y in tb) or (y in ta and x in tb):
            out.append((5, "立場用語對立",
                        f"同一事，{ra['book']}記作「{x if x in ta else y}」、"
                        f"{rb['book']}記作「{y if x in ta else x}」。春秋筆法之直接證據。"))
    if "蔑稱" in ta or "蔑稱" in tb:
        out.append((2, "蔑稱", "一方使用貶稱，敘述已帶判斷。"))

    ea, eb = aa["acts"], ab["acts"]
    if ea and eb and not (ea & eb):
        out.append((3, "行為不重疊",
                    f"{ra['book']}記「{'、'.join(sorted(ea))}」／"
                    f"{rb['book']}記「{'、'.join(sorted(eb))}」。同一事而所記不同。"))
    return out


def align(rows, threshold=4, cross_stance_only=True):
    A = [anchors(r) for r in rows]
    pairs = []
    for i, j in itertools.combinations(range(len(rows)), 2):
        if cross_stance_only and A[i]["stance"] == A[j]["stance"]:
            continue
        s, why = pair_score(A[i], A[j])
        if s < threshold:
            continue
        d = diverge(rows[i], rows[j], A[i], A[j])
        pairs.append({"a": rows[i], "b": rows[j], "score": s,
                      "anchors": why, "diverge": d,
                      "weight": s + sum(w for w, _, _ in d)})
    return sorted(pairs, key=lambda p: -p["weight"])


def show(pairs, fh=sys.stdout):
    print(f"\n{'='*70}\n事件對照 {len(pairs)} 組\n{'='*70}", file=fh)
    for p in pairs:
        a, b = p["a"], p["b"]
        print(f"\n■ 權重 {p['weight']}　錨定：{'；'.join(p['anchors'])}", file=fh)
        for w, k, why in p["diverge"]:
            print(f"  {'⚠' if w >= 4 else '·'} {k}　{why}", file=fh)
        for r in (a, b):
            print(f"\n    〔{r['stance']}〕《{r['book']}·{r['juan']}》"
                  f" {r.get('time') or '—'}", file=fh)
            print(f"      「{r['evidence']}」", file=fh)
        print("  " + "─" * 66, file=fh)


def main():
    ap = argparse.ArgumentParser(description="對照 — 事件層並置")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("eras", help="紀年換算")
    e.add_argument("terms", nargs="+")
    e.add_argument("--book", help="來源書名，用於同名年號消歧，如 晋书")

    g = sub.add_parser("align", help="事件對照")
    g.add_argument("findings", help="huijian run 產出的 findings.json")
    g.add_argument("-t", "--threshold", type=int, default=4)
    g.add_argument("-o", "--out")

    a = ap.parse_args()
    if a.cmd == "eras":
        for t in a.terms:
            y = to_year(t, a.book)
            if y:
                print(f"{t:16s} → {y[0]} 年（{y[1]}{y[2]}）")
                continue
            cs = era_candidates(t)
            if not cs:
                print(f"{t:16s} → 未收，不猜")
            else:
                opts = "／".join(str(c[0]) for c in cs if c[1] == cs[0][1])
                print(f"{t:16s} → 同名年號「{cs[0][1]}」候選 {opts} —— 不猜"
                      f"（加 --book 書名消歧）")
        return

    data = json.load(open(a.findings, encoding="utf-8"))
    rows = [h for p in data for h in p["hits"]] if data and "hits" in data[0] else data
    pairs = align(rows, a.threshold)
    show(pairs)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            show(pairs, fh)
        print(f"\n→ {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
