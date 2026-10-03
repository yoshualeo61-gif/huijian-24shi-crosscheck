#!/usr/bin/env python3
"""回歸測試：三處已修缺陷。

不需要語料，不需要 API key。直接跑：

    python tests/test_fixes.py
"""

import collections, json, os, pathlib, shutil, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import huijian, duizhao, biaozhu

FAIL = []


def check(cond, label, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL.append(f"{label} {detail}".strip())


# ── 1. 檢索去重不得跨書丟段 ────────────────────────────────
# 《南史》刪削《宋書》而成，開頭成段雷同。舊版以 ctx[:45] 做全局 key，
# 雷同者只留排序在前的一本，被丟掉的往往正是原始史源。
def test_scan_keeps_parallel_passages():
    print("1. 跨書雷同段落不得被去重丟掉")
    shared = ("元嘉二十七年，太祖遣衆軍北伐，以王玄謨爲寧朔將軍，前鋒入河，"
              "受輔國將軍蕭斌節度。王玄謨進圍滑臺，魏主自率大衆來救，"
              "王玄謨懼而退走，爲魏所乘，死者萬餘人。")
    assert len(shared) > 45, "前綴須長於去重 key，否則測不到"
    books = {
        "宋书": ("列传_卷七十六.txt", shared + "斌將斬之，沈慶之諫，乃免。"),
        "南史": ("卷十六.txt", shared + "斌欲斬之，沈慶之固諫，乃止。"),
        "魏书": ("卷九十七.txt", "太平真君十一年，世祖南伐，宋將王玄謨遁走，棄軍資巨萬。"),
    }
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        for b, (fn, txt) in books.items():
            d = pathlib.Path("corpus") / b
            d.mkdir(parents=True)
            (d / fn).write_text(txt, encoding="utf-8")
        hits = huijian.scan(["王玄謨"], window=200)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    got = sorted({h[0] for h in hits})
    check(got == ["南史", "宋书", "魏书"], "三書各留一段", f"got {got}")
    check(len(hits) == 3, "命中段數為 3", f"got {len(hits)}")


# ── 1b. 同書之內的重複檔仍須摺疊 ──────────────────────────
# 語料裡同一卷可能落成兩個檔名（南史_卷一 / 南史_-卷一），
# 內容相同。去重限定在書之內，正是為了這種情形。
def test_scan_still_folds_duplicates_within_a_book():
    print("1b. 同書之內內容相同的重複檔仍被摺疊")
    txt = ("元嘉二十七年，太祖遣衆軍北伐，以王玄謨爲寧朔將軍，前鋒入河，"
           "受輔國將軍蕭斌節度。王玄謨懼而退走，爲魏所乘。")
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        d = pathlib.Path("corpus") / "南史"
        d.mkdir(parents=True)
        for fn in ("南史_卷一-原文.txt", "南史_-卷一-原文.txt"):
            (d / fn).write_text(txt, encoding="utf-8")
        hits = huijian.scan(["王玄謨"], window=200)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    check(len(hits) == 1, "重複檔只留一段", f"got {len(hits)}")


# ── 2. 同名年號須消歧，不得悄悄換算成另一朝 ────────────────
def test_era_disambiguation():
    print("2. 同名年號靠書名消歧，否則不猜")
    cases = [
        ("泰始二年", "晋书", 266),   # 西晉武帝
        ("泰始二年", "宋书", 466),   # 劉宋明帝
        ("建武二年", "后汉书", 26),  # 東漢光武
        ("建武二年", "南齐书", 495),  # 南齊明帝
        ("建武二年", "晋书", 318),   # 東晉元帝
        ("延昌二年", "魏书", 513),   # 無同名，不依賴書名
    ]
    for text, book, want in cases:
        y = duizhao.to_year(text, book)
        check(y is not None and y[0] == want,
              f"《{book}》{text} → {want}", f"got {y}")

    check(duizhao.to_year("泰始二年") is None,
          "泰始二年 無書名 → None（不猜）")
    check(duizhao.to_year("乾隆五十年", "明史") is None,
          "未收年號 → None（不猜）")
    check(duizhao.to_year("中大通二年", "梁书") == (530, "中大通", 2),
          "長年號優先，中大通不被大通吃掉")


# ── 3. 同名年號不得憑空造出「同年」錨定 ────────────────────
def test_no_phantom_year_anchor():
    print("3. 錯誤年份不得製造對齊錨定")
    rows = [
        {"person": "某甲", "place": "洛阳", "time": "泰始二年", "acts": ["除授"],
         "evidence": "泰始二年，拜太尉", "book": "晋书", "juan": "卷三",
         "stance": "唐修"},
        {"person": "某甲", "place": "", "time": "泰始二年", "acts": [],
         "evidence": "泰始二年，征為太尉", "book": "宋书", "juan": "卷六",
         "stance": "南朝系"},
    ]
    a, b = duizhao.anchors(rows[0]), duizhao.anchors(rows[1])
    check(a["year"][0] == 266 and b["year"][0] == 466,
          "兩書的泰始二年分別換算為 266／466",
          f"got {a['year']} {b['year']}")
    s, why = duizhao.pair_score(a, b)
    check(not any("同年" in w for w in why), "不得判為同年", f"got {why}")


# ── 4. 《南史》《北史》為唐修，非南北朝當代證人 ────────────
def test_stance_of_derivative_histories():
    print("4. 立場表：派生本不計入南北朝系")
    check(huijian.STANCE["南史"] == "唐修", "南史 → 唐修",
          huijian.STANCE["南史"])
    check(huijian.STANCE["北史"] == "唐修", "北史 → 唐修",
          huijian.STANCE["北史"])
    check(huijian.STANCE["宋书"] == "南朝系", "宋书 仍為南朝系")
    check(huijian.STANCE["魏书"] == "北朝系", "魏书 仍為北朝系")


# ── 5. 逐字校驗閘仍攔得住杜撰與改寫 ────────────────────────
def test_evidence_gate():
    print("5. 逐字校驗閘")
    chunk = "王玄謨懼而退走，爲魏所乘，死者萬餘人。"
    ok = "王玄謨懼而退走"                 # 逐字
    bad_fabricated = "玄謨坐法伏誅，梟首于市"   # 杜撰
    bad_paraphrase = "王玄謨因恐懼而撤退"       # 改寫
    check(ok in chunk, "逐字引文通過")
    check(bad_fabricated not in chunk, "杜撰引文被攔")
    check(bad_paraphrase not in chunk, "改寫引文被攔")


# ── 6. 偏移錨定：跨接縫的拼接引文必須被攔下 ────────────────
# 一個 chunk 可能由同卷中數個不相鄰的窗口拼成。舊版用子串測試
# `ev in chunk`，橫跨接縫的引文在 chunk 裡看似連續，就這麼過了關。
def test_offset_anchoring_rejects_splice():
    print("6. 跨接縫拼接的引文被攔下")
    far = "甲" * 900                          # 兩段相距遠超窗口，不會併段
    text = ("元嘉二十七年，王玄謨進圍滑臺，魏主自率大衆來救。"
            + far +
            "泰始二年，王玄謨遷領軍將軍，卒於官。")
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        d = pathlib.Path("corpus") / "宋书"
        d.mkdir(parents=True)
        (d / "列传_卷七十六.txt").write_text(text, encoding="utf-8")
        hits = huijian.scan(["王玄謨"], window=60)
        cks = huijian._mkchunks(hits)
        check(len(cks) == 1, "兩窗口落在同一 chunk", f"got {len(cks)}")
        c = cks[0]
        check(len(c["segments"]) == 2, "該 chunk 含兩個不相鄰的段",
              f"got {len(c['segments'])}")

        s0, s1 = c["segments"][0]["text"], c["segments"][1]["text"]
        good = s0[:12]
        spliced = s0[-8:] + huijian.SEAM + s1[:8]

        check(huijian.locate(good, c) is not None, "段內引文通過")
        # 舊閘門會放過它：它確實是 chunk 的子串
        check(spliced in c["chunk"], "拼接引文確實是 chunk 的子串（舊閘門會放過）")
        check(huijian.locate(spliced, c) is None, "新閘門攔下跨縫拼接")

        # 地址必須能回原卷重新切片核對
        a, b = huijian.locate(good, c)
        row = {"src": c["src"], "ev_start": a, "ev_end": b, "evidence": good}
        check(huijian.strict_check(row) is True, "按地址重新切片逐字相符",
              f"got {huijian.strict_check(row)}")
        bad = dict(row, evidence="王玄謨伏誅於市")
        check(huijian.strict_check(bad) is False, "地址對不上內容時判否")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


# ── 7. 人名歸一：未歸一會誤報雙邊獨載 ──────────────────────
def test_person_normalization():
    print("7. 人名歸一候選與合併")
    rows = [
        {"person": "善明", "acts": ["筑城"], "time": "", "evidence": "善明築城於朐",
         "book": "宋书", "juan": "卷八十八", "stance": "南朝系"},
        {"person": "劉善明", "acts": ["被杀"], "time": "", "evidence": "劉善明見殺",
         "book": "魏书", "juan": "卷六十一", "stance": "北朝系"},
    ]
    cands = huijian.alias_candidates(rows)
    check(len(cands) == 1, "提出一組候選", f"got {len(cands)}")
    check(cands[0]["short"] == "善明" and cands[0]["suggest"] == "劉善明",
          "善明 → 劉善明", str(cands[0].get("suggest")))
    check(cands[0]["ambiguous"] is False, "唯一候選不標 ambiguous")

    # 未歸一：兩條各自成人，兩邊都被誤報「僅見於」
    before = huijian.analyse([dict(r) for r in rows], ["南朝系", "北朝系"])
    flags_before = [k for p in before for _, k, _ in p["flags"]]
    check(len(before) == 2, "未歸一時算成兩個人", f"got {len(before)}")
    check(sum("僅見於" in k for k in flags_before) == 2,
          "未歸一時兩邊都誤報僅見於", str(flags_before))

    # 歸一後：一個人，跨立場，誤報消失
    merged = [dict(r) for r in rows]
    n = huijian.apply_aliases(merged, {"善明": "劉善明"})
    check(n == 1, "改寫一條記載", f"got {n}")
    check(merged[0]["person_raw"] == "善明", "原寫法留在 person_raw")
    after = huijian.analyse(merged, ["南朝系", "北朝系"])
    flags_after = [k for p in after for _, k, _ in p["flags"]]
    check(len(after) == 1, "歸一後算成一個人", f"got {len(after)}")
    check(after[0]["stances"] == ["北朝系", "南朝系"], "跨立場成立",
          str(after[0]["stances"]))
    check(not any("僅見於" in k for k in flags_after),
          "僅見於誤報消失", str(flags_after))


# ── 8. 一短名對上多個長名時不給建議 ────────────────────────
def test_ambiguous_alias_not_suggested():
    print("8. 同名歧義不自作主張")
    rows = [
        {"person": "善明", "evidence": "x", "book": "宋书", "juan": "一",
         "stance": "南朝系"},
        {"person": "劉善明", "evidence": "y", "book": "魏书", "juan": "二",
         "stance": "北朝系"},
        {"person": "王善明", "evidence": "z", "book": "梁书", "juan": "三",
         "stance": "南朝系"},
    ]
    c = [x for x in huijian.alias_candidates(rows) if x["short"] == "善明"][0]
    check(c["ambiguous"] is True, "標為 ambiguous")
    check(c["suggest"] is None, "不給建議值", str(c["suggest"]))
    check(sorted(c["candidates"]) == ["劉善明", "王善明"], "兩個候選都列出",
          str(c["candidates"]))


# ── 9. 月份與干支日：現成的強錨，此前一分未得 ────────────────
def test_month_and_ganzhi_anchors():
    print("9. 月與干支日錨定")
    check(duizhao.parse_month_day("二月丁卯") == (2, False, "丁卯"),
          "二月丁卯", str(duizhao.parse_month_day("二月丁卯")))
    check(duizhao.parse_month_day("二月，丁卯，虜寇壽陽") == (2, False, "丁卯"),
          "月與干支間有標點也認得")
    check(duizhao.parse_month_day("閏二月甲子") == (2, True, "甲子"),
          "閏月", str(duizhao.parse_month_day("閏二月甲子")))
    check(duizhao.parse_month_day("春正月乙巳") == (1, False, "乙巳"), "春正月")
    check(duizhao.parse_month_day("十二月壬戌朔") == (12, False, "壬戌"), "十二月")
    # 干支只在月份之後近處採信：月前的干支屬別日，不可冒領
    check(duizhao.parse_month_day("癸卯，詔北伐。二月，丁卯，虜寇壽陽")
          == (2, False, "丁卯"), "月前的干支不被冒領")
    check(duizhao.parse_month_day("元嘉二十七年") == (None, False, None),
          "只有年份時不編造月日")

    rows = [
        {"person": "垣崇祖", "place": "壽陽", "time": "建元二年", "acts": ["征战"],
         "evidence": "二月，丁卯，虜寇壽陽，豫州刺史垣崇祖破走之",
         "book": "南齐书", "juan": "本纪卷二", "stance": "南朝系"},
        {"person": "垣崇祖", "place": "壽陽", "time": "建元二年", "acts": ["征战"],
         "evidence": "二月丁卯，魏軍攻壽陽，豫州刺史垣崇祖破走之",
         "book": "南史", "juan": "卷四", "stance": "唐修"},
    ]
    a, b = duizhao.anchors(rows[0]), duizhao.anchors(rows[1])
    sc, why = duizhao.pair_score(a, b)
    check(any("同月" in w for w in why), "同月計入錨定", str(why))
    check(any("同日 丁卯" in w for w in why), "同日計入錨定", str(why))
    check(sc >= 11, f"錨定分數提高到 {sc}（原為 6）", str(sc))


# ── 10. 日次／閏月歧異與改元元年降權 ────────────────────────
def test_day_divergence_and_era_boundary():
    print("10. 日次歧異與改元元年降權")
    mk = lambda bk, ev, t: {"person": "某", "place": "", "acts": [], "time": t,
                            "evidence": ev, "book": bk, "juan": "一",
                            "stance": "南朝系" if bk != "魏书" else "北朝系"}
    ra, rb = mk("南齐书", "二月丁卯，戰", "建元二年"), mk("魏书", "二月己巳，戰", "建元二年")
    d = duizhao.diverge(ra, rb, duizhao.anchors(ra), duizhao.anchors(rb))
    check(any(k == "日次歧異" for _, k, _ in d), "同年同月異干支 → 日次歧異",
          str([k for _, k, _ in d]))

    ra, rb = mk("南齐书", "閏二月丁卯", "建元二年"), mk("魏书", "二月丁卯", "建元二年")
    d = duizhao.diverge(ra, rb, duizhao.anchors(ra), duizhao.anchors(rb))
    check(any(k == "閏月歧異" for _, k, _ in d), "閏月之別被標出",
          str([k for _, k, _ in d]))

    # 差一年又涉改元元年：很可能只是改元月份算法之異，降權
    ra = mk("南齐书", "x", "建元元年")
    rb = mk("魏书", "y", "太和三年")
    aa, ab = duizhao.anchors(ra), duizhao.anchors(rb)
    check(aa["year"][0] == 479 and ab["year"][0] == 479, "建元元年與太和三年同為 479",
          f"{aa['year']} {ab['year']}")
    ra2 = mk("南齐书", "x", "建元元年")
    rb2 = mk("魏书", "y", "太和四年")                      # 480，差一年
    d = duizhao.diverge(ra2, rb2, duizhao.anchors(ra2), duizhao.anchors(rb2))
    yr = [(w, k) for w, k, _ in d if k == "紀年歧異"]
    check(yr and yr[0][0] == 2, "差一年且一方為改元元年 → 權重降為 2", str(yr))


# ── 11. 基線：著錄率低的書，其沉默不得當證據 ────────────────
def test_baseline_downweights_silence():
    print("11. 基線校準終局獨載")
    rows = [
        {"person": "甲", "acts": ["被杀"], "time": "", "evidence": "甲見殺",
         "book": "魏书", "juan": "一", "stance": "北朝系"},
        {"person": "甲", "acts": ["赴任"], "time": "", "evidence": "甲之任",
         "book": "宋书", "juan": "二", "stance": "南朝系"},
    ]
    # 無基線：權重 5，並說明無從比對
    r0 = huijian.analyse([dict(x) for x in rows], ["南朝系", "北朝系"])
    f0 = [(w, k) for p in r0 for w, k, _ in p["flags"] if "終局獨載" in k]
    check(f0 and f0[0][0] == 5, "無基線時權重 5", str(f0))

    # 基線低：宋书本就少記終局，沉默不足為奇 → 降為 2
    low = {"宋书": {"n": 40, "k": 2, "rate": 0.05}}
    r1 = huijian.analyse([dict(x) for x in rows], ["南朝系", "北朝系"], low)
    f1 = [(w, k) for p in r1 for w, k, _ in p["flags"] if "終局獨載" in k]
    check(f1 and f1[0][0] == 2, "著錄率 5% → 權重降為 2", str(f1))

    # 基線高：沉默確屬異常 → 維持 5
    high = {"宋书": {"n": 40, "k": 36, "rate": 0.9}}
    r2 = huijian.analyse([dict(x) for x in rows], ["南朝系", "北朝系"], high)
    f2 = [(w, k) for p in r2 for w, k, _ in p["flags"] if "終局獨載" in k]
    check(f2 and f2[0][0] == 5, "著錄率 90% → 維持權重 5", str(f2))
    why2 = [y for p in r2 for _, k, y in p["flags"] if "終局獨載" in k][0]
    check("90%" in why2, "基線數字寫進理由，可被指著反駁", why2[:60])


def test_terminal_rate_counts():
    print("11b. 終局著錄率統計")
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        (pathlib.Path("corpus") / "魏书").mkdir(parents=True)
        (pathlib.Path("corpus") / "宋书").mkdir(parents=True)
        # 兩人之間填足距離，否則 window 會把兩人的上下文疊在一起
        pathlib.Path("corpus/魏书/一.txt").write_text(
            "甲戍郁洲，後見殺。" + "紀事" * 150 + "乙鎮壽陽，有功，久之去職。",
            encoding="utf-8")
        pathlib.Path("corpus/宋书/一.txt").write_text(
            "甲之任，乙築城。", encoding="utf-8")
        base = huijian.terminal_rate(["甲", "乙"], window=120)

        # 中間夾著別人時，不得把鄰人的死算到自己頭上
        (pathlib.Path("corpus") / "北齐书").mkdir(parents=True)
        pathlib.Path("corpus/北齐书/一.txt").write_text(
            "丙與乙俱出，乙見殺。", encoding="utf-8")
        near = huijian.terminal_rate(["丙", "乙"], window=60)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    check(base["魏书"]["n"] == 2 and base["魏书"]["k"] == 1,
          "魏书：提及 2 人，記其終 1 人", str(base["魏书"]))
    check(abs(base["魏书"]["rate"] - 0.5) < 1e-9, "著錄率 50%",
          str(base["魏书"]["rate"]))
    check(base["宋书"]["k"] == 0, "宋书：無終局用語", str(base["宋书"]))
    check(near["北齐书"]["k"] == 1, "夾著別人時只算得一人",
          str(near["北齐书"]))


# ── 12. 置換檢驗：類別名必須剝掉立場，否則分布會碎掉 ────────
def test_flag_kind_strips_stance():
    print("12. 旗標類別名歸併")
    check(huijian._flag_kind("僅見於「南朝系」") == "僅見於", "剝掉立場名",
          huijian._flag_kind("僅見於「南朝系」"))
    check(huijian._flag_kind("僅見於「南朝系」")
          == huijian._flag_kind("僅見於「北朝系」"),
          "不同立場歸為同一類（否則虛無分布碎掉）")
    check(huijian._flag_kind("終局獨載（归降）：僅「北朝系」有載") == "終局獨載",
          "剝掉行為名與後綴",
          huijian._flag_kind("終局獨載（归降）：僅「北朝系」有載"))
    check(huijian._flag_kind("終局互斥：死于战 ↔ 归降") == "終局互斥", "終局互斥")
    check(huijian._flag_kind("紀年歧異（除授）：480 / 481") == "紀年歧異", "紀年歧異")


def test_permutation_is_sensitive():
    print("12b. 置換檢驗對立場結構敏感")
    # 甲只見於南朝系兩書，乙只見於北朝系兩書 → 實測兩條「僅見於」
    rows = []
    for bk, st, who in [("宋书", "南朝系", "甲"), ("南齐书", "南朝系", "甲"),
                        ("魏书", "北朝系", "乙"), ("北齐书", "北朝系", "乙")]:
        rows.append({"person": who, "acts": [], "time": "", "evidence": f"{who}事",
                     "book": bk, "juan": "一", "stance": st})
    obs = huijian.flag_counts(rows, ["南朝系", "北朝系"])
    check(obs["僅見於"] == 2, "實測兩條僅見於", str(dict(obs)))

    # 換成每人各跨兩立場 → 僅見於應歸零
    amap = {"宋书": "南朝系", "南齐书": "北朝系",
            "魏书": "北朝系", "北齐书": "南朝系"}
    perm = [dict(r, stance=amap[r["book"]]) for r in rows]
    c = huijian.flag_counts(perm, ["南朝系", "北朝系"])
    check(c["僅見於"] == 0, "重排立場後僅見於歸零", str(dict(c)))
    check(obs["僅見於"] > c["僅見於"],
          "檢驗確實對立場結構敏感（否則 p 值毫無意義）")


# ── 13. 統計：區間與 κ 必須算對，整個標注環節的結論都靠它 ──────
def test_wilson_interval():
    print("13. Wilson 區間")
    lo, hi = biaozhu.wilson(9, 10)
    check(0.55 < lo < 0.60 and 0.97 < hi <= 1.0,
          "9/10 → 約 [0.56, 0.98]", f"[{lo:.3f}, {hi:.3f}]")
    lo, hi = biaozhu.wilson(135, 150)
    check(0.84 < lo < 0.86 and 0.93 < hi < 0.95,
          "135/150 → 約 [0.85, 0.94]", f"[{lo:.3f}, {hi:.3f}]")
    lo, hi = biaozhu.wilson(0, 10)
    check(lo == 0.0 and 0.25 < hi < 0.35, "k=0 不退化成 [0,0]",
          f"[{lo:.3f}, {hi:.3f}]")
    lo, hi = biaozhu.wilson(10, 10)
    check(hi == 1.0 and 0.65 < lo < 0.75, "k=n 不退化成 [1,1]",
          f"[{lo:.3f}, {hi:.3f}]")
    lo, hi = biaozhu.wilson(0, 0)
    check((lo, hi) == (0.0, 1.0), "n=0 回傳全區間")
    # 樣本量越大區間越窄 —— 這是建議 n=150 的根據
    w30 = biaozhu.wilson(27, 30)
    w150 = biaozhu.wilson(135, 150)
    check((w150[1] - w150[0]) < (w30[1] - w30[0]),
          "n 越大區間越窄", f"{w30} vs {w150}")


def test_kappa():
    print("13b. Cohen's κ")
    check(biaozhu.kappa([("ok", "ok"), ("wrong", "wrong")] * 5) == 1.0,
          "完全一致 → κ=1")
    k = biaozhu.kappa([("ok", "wrong"), ("wrong", "ok")] * 5)
    check(k is not None and k < 0, "系統性相反 → κ<0", str(k))
    # 兩人都只用一個類別：po=1 但 pe=1，κ 無定義（不可報成 1.0）
    check(biaozhu.kappa([("ok", "ok")] * 10) is None,
          "僅用單一類別 → None，不冒充完美一致")
    check(biaozhu.kappa([]) is None, "空輸入 → None")
    # 期望值在測試裡獨立算一遍，不靠寫測試時的心算
    pairs = ([("a", "a")] * 5 + [("b", "b")] * 2
             + [("a", "b")] * 2 + [("b", "a")] * 1)
    n = len(pairs)
    po = sum(1 for x, y in pairs if x == y) / n
    pa = {c: sum(1 for x, _ in pairs if x == c) / n for c in "ab"}
    pb = {c: sum(1 for _, y in pairs if y == c) / n for c in "ab"}
    pe = sum(pa[c] * pb[c] for c in "ab")
    want = (po - pe) / (1 - pe)
    k = biaozhu.kappa(pairs)
    check(abs(k - want) < 1e-12,
          f"與獨立算出的 κ={want:.4f} 相符", f"got {k:.4f}")
    check(abs(want - 0.3478) < 1e-3, "該例的 κ 約為 0.348", f"{want:.4f}")


def test_stratified_sampling():
    print("13c. 分層抽樣")
    pool = ([{"g": "多", "i": i} for i in range(90)]
            + [{"g": "少", "i": i} for i in range(10)])
    pick = biaozhu.stratified(pool, 20, lambda x: x["g"], seed=0)
    check(len(pick) == 20, "抽到 20 條", str(len(pick)))
    gs = collections.Counter(x["g"] for x in pick)
    check(gs["少"] >= 1, "罕見層沒被抽空（純隨機很可能一條不中）", str(dict(gs)))
    check(abs(gs["多"] - 18) <= 1 and abs(gs["少"] - 2) <= 1,
          "按比例分配 18/2", str(dict(gs)))
    # 同種子可復現，不同種子會換樣本
    again = biaozhu.stratified(pool, 20, lambda x: x["g"], seed=0)
    check([x["i"] for x in pick] == [x["i"] for x in again], "同種子可復現")
    other = biaozhu.stratified(pool, 20, lambda x: x["g"], seed=7)
    check([x["i"] for x in pick] != [x["i"] for x in other], "不同種子換樣本")
    check(len(biaozhu.stratified(pool, 500, lambda x: x["g"])) == 100,
          "n 大於總量時全取")


def test_annotation_values_are_validated():
    print("13d. 標注值校驗")
    good = [{"id": "A0001", "track": "A",
             "verdict": {"person": "ok", "overall": "ok"}}]
    check(biaozhu._check(good) == [], "合法值通過")
    typo = [{"id": "A0002", "track": "A",
             "verdict": {"person": "OK", "overall": "ok"}}]
    bad = biaozhu._check(typo)
    check(len(bad) == 1 and "A0002" in bad[0],
          "拼錯的值被指出，而不是悄悄略過（否則分母會無聲縮水）", str(bad))
    partial = [{"id": "A0003", "track": "A",
                "verdict": {"person": None, "overall": None}}]
    check(biaozhu._check(partial) == [], "未標注（null）不算錯")


def test_rate_excludes_na():
    print("13e. 計分：na 不計入分母")
    items = [
        {"id": "A1", "track": "A", "verdict": {"place": "na"}},
        {"id": "A2", "track": "A", "verdict": {"place": "ok"}},
        {"id": "A3", "track": "A", "verdict": {"place": "wrong"}},
        {"id": "A4", "track": "A", "verdict": {"place": None}},
    ]
    r = biaozhu._rate(items, "A", "place", {"ok"})
    check(r["n"] == 2 and r["k"] == 1,
          "na 與未標注都不入分母 → 1/2", str(r))
    check(biaozhu._rate(items, "A", "time", {"ok"}) is None,
          "全未標注 → None，不報 0%")


def test_sample_judges_extracted_name():
    print("13f. A 軌判原始抽取的人名，不判歸一後的")
    w = pathlib.Path(tempfile.mkdtemp())
    try:
        findings = [{"person": "劉善明", "stances": ["南朝系"], "acts": ["筑城"],
                     "flags": [[3, "僅見於「南朝系」", "理由"]],
                     "hits": [{"person": "劉善明", "person_raw": "善明",
                               "book": "宋书", "juan": "一", "stance": "南朝系",
                               "acts": ["筑城"], "time": "", "place": "",
                               "evidence": "善明築城"}],
                     "score": 3}]
        json.dump(findings, open(w / "findings.json", "w", encoding="utf-8"),
                  ensure_ascii=False)
        items = biaozhu.build_sample(w, 10, 10, 10, 0, huijian.TERMINAL)
    finally:
        shutil.rmtree(w, ignore_errors=True)
    a = [i for i in items if i["track"] == "A"][0]
    check(a["person"] == "善明", "待判的是抽取原形「善明」", a["person"])
    check(a["person_normalized"] == "劉善明", "歸一後的形另存，供參考",
          a["person_normalized"])


if __name__ == "__main__":
    for t in (test_scan_keeps_parallel_passages,
              test_scan_still_folds_duplicates_within_a_book,
              test_era_disambiguation,
              test_no_phantom_year_anchor, test_stance_of_derivative_histories,
              test_evidence_gate,
              test_offset_anchoring_rejects_splice,
              test_person_normalization,
              test_ambiguous_alias_not_suggested,
              test_month_and_ganzhi_anchors,
              test_day_divergence_and_era_boundary,
              test_baseline_downweights_silence,
              test_terminal_rate_counts,
              test_flag_kind_strips_stance,
              test_permutation_is_sensitive,
              test_wilson_interval,
              test_kappa,
              test_stratified_sampling,
              test_annotation_values_are_validated,
              test_rate_excludes_na,
              test_sample_judges_extracted_name):
        t()
        print()
    if FAIL:
        print(f"{len(FAIL)} 項失敗：")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("全部通過")
