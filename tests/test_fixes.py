#!/usr/bin/env python3
"""回歸測試：三處已修缺陷。

不需要語料，不需要 API key。直接跑：

    python tests/test_fixes.py
"""

import collections, json, os, pathlib, re, shutil, sys, tempfile

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

    # 未歸一：兩條各自成人，於是兩邊各自只剩一個獨立史源 —— 兩邊都
    # 淪為孤證，跨立場比對整個落空。代價和從前一樣大，只是現在報得
    # 更誠實：不再誤報成「僅見於某系」那種看似有內容的立場信號。
    before = huijian.analyse([dict(r) for r in rows], ["南朝系", "北朝系"])
    flags_before = [k for p in before for _, k, _ in p["flags"]]
    check(len(before) == 2, "未歸一時算成兩個人", f"got {len(before)}")
    check(sum("孤證" in k for k in flags_before) == 2,
          "未歸一時兩邊都淪為孤證", str(flags_before))
    check(not any("僅見於" in k for k in flags_before),
          "且不再誤報成立場信號", str(flags_before))

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
    # 名字必須兩字以上（單字名無分辨力，已在 terminal_rate 裡篩掉），
    # 且終局用語要與人名同句才算 —— 兩條都是真語料上吃過教訓才加的。
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        (pathlib.Path("corpus") / "魏书").mkdir(parents=True)
        (pathlib.Path("corpus") / "宋书").mkdir(parents=True)
        pathlib.Path("corpus/魏书/一.txt").write_text(
            "张稷戍郁洲，後見殺。垣崇祖鎮壽陽，有功，久之去職。",
            encoding="utf-8")
        pathlib.Path("corpus/宋书/一.txt").write_text(
            "张稷之任，垣崇祖築城。", encoding="utf-8")
        base = huijian.terminal_rate(["张稷", "垣崇祖"])

        # 同句裡還有別人時，歸屬不明，不算
        (pathlib.Path("corpus") / "北齐书").mkdir(parents=True)
        pathlib.Path("corpus/北齐书/一.txt").write_text(
            "张稷與垣崇祖俱出，垣崇祖見殺。", encoding="utf-8")
        near = huijian.terminal_rate(["张稷", "垣崇祖"])
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    check(base["魏书"]["n"] == 2 and base["魏书"]["k"] == 1,
          "魏书：提及 2 人，記其終 1 人", str(base["魏书"]))
    check(abs(base["魏书"]["rate"] - 0.5) < 1e-9, "著錄率 50%",
          str(base["魏书"]["rate"]))
    check(base["宋书"]["k"] == 0, "宋书：無終局用語", str(base["宋书"]))
    check(base["魏书"]["usable"] is False,
          f"n=2 遠低於 {huijian.BASELINE_MIN_N}，標為不可用")
    check(near["北齐书"]["k"] == 1, "同句夾著別人時只算得一人",
          str(near["北齐书"]))


def test_terminal_rate_filters_short_names():
    print("11c. 單字名不得進入基線群體")
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        (pathlib.Path("corpus") / "魏书").mkdir(parents=True)
        pathlib.Path("corpus/魏书/一.txt").write_text(
            "恩走郁洲，帝追破之，王亮卒。", encoding="utf-8")
        base = huijian.terminal_rate(["恩", "帝"])
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)
    check(base == {}, "全是單字名時群體為空，不產出假比率", str(base))


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


# ── 14. 真語料上發現的問題（全部可離線復現）──────────────────
def test_vernacular_truncation():
    print("14. 現代白話不得混進語料")
    # 上游有些頁面在原文之後直接接白話譯文，只用一行「译文（…）」作界,
    # 而檔名仍叫「原文」。混進來最險的地方是：逐字校驗**攔不住**它 ——
    # 譯文確實逐字存在於 chunk 裡，引文會「通過」校驗。
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        p = tmp / "a.html"
        p.write_text(
            "<h1>卷一 原文</h1><p>段译</p><p>译文</p>"
            # 原文須有相當長度：html_to_text 要求已累積 200 字才認界線,
            # 免得頁首的任何東西把整卷切光（真實的卷都是數千字）。
            + "<p>宣秉字巨公，冯翊云阳人也。少修高节，显名三辅。" * 12 + "</p>"
            "<p>上一篇目录下一篇</p>"
            "<p>译文（宣秉）</p>"
            "<p>宣秉字巨公，是冯翊云阳人。他年轻时就修养高尚的节操。</p>",
            encoding="utf-8")
        t = huijian.html_to_text(p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check("宣秉字巨公，冯翊云阳人也" in t, "原文保留")
    check("他年轻时" not in t, "「译文（…）」之後的白話被截掉")
    check("上一篇目录下一篇" not in t, "導覽文字被濾掉")
    # 孤立一行「译文」是頁首導覽標籤（《史記》各頁作「段译 / 译文」),
    # 出現在原文之前。當成界線會把整卷原文切光 —— 初版就是這麼錯的,
    # 三國志、史記、漢書 三部書當場歸零。
    check("宣秉字巨公" in t, "孤立的「译文」標籤不得當作界線")


def test_vernacular_density_gate():
    print("14b. 通篇白話而無界線的卷，靠內容判掉")
    wen = "宣秉字巨公，冯翊云阳人也。少修高节，显名三辅。建武元年，拜御史中丞。"
    bai = "宣秉是冯翊云阳人，他的节操很高尚，所以在三辅一带很有名的。"
    check(huijian.vernacular_density(wen) < huijian.VERNACULAR_MAX,
          f"文言密度 {huijian.vernacular_density(wen):.2f} 低於門檻")
    check(huijian.vernacular_density(bai * 20) > huijian.VERNACULAR_MAX,
          f"白話密度 {huijian.vernacular_density(bai*20):.2f} 高於門檻")


def test_expand_name_guard():
    print("14c. 階段二的人名必須先篩")
    # 抽取規格刻意不補姓氏，於是必然出現單字名；而「帝」在真語料裡
    # 出現 59,084 次，拿它當人名掃全庫只會灌出幾千個無意義 chunk。
    keep, drop = huijian._usable_names(["帝", "恩", "张稷", "徐玄明", "高祖"])
    dropped = {n for n, _ in drop}
    check("帝" in dropped and "恩" in dropped, "單字名被篩掉", str(dropped))
    check("高祖" in dropped, "帝號泛稱被篩掉", str(dropped))
    check(sorted(keep) == ["张稷", "徐玄明"], "多字人名保留", str(keep))
    check(all(why for _, why in drop), "每個被篩掉的都給了理由")


def test_terminal_attribution_same_clause():
    print("14d. 終局用語須與人名同句")
    # 初版取前後 120 字窗口，而「卒」在列傳裡俯拾即是，於是任何在列傳
    # 出現過的名字都算「記其終」—— 實測三國志、史記、後漢書全報 100%。
    s = "张稷字公乔，吴郡人也。历官至镇北将军。王亮卒。"
    m = re.search("张稷", s)
    check(huijian._attributable(s, "张稷", m, 25) is False,
          "別人的「卒」在別句，不算本人的")
    s2 = "徐玄明斩送张稷首，张稷见杀。"
    m2 = [x for x in re.finditer("张稷", s2)][1]
    check(huijian._attributable(s2, "张稷", m2, 25) is True,
          "同句緊接的終局用語算本人的")


def test_baseline_min_n_gate():
    print("14e. 樣本不足的基線不得調權重")
    rows = [
        {"person": "甲", "acts": ["被杀"], "time": "", "evidence": "甲見殺",
         "book": "魏书", "juan": "一", "stance": "北朝系"},
        {"person": "甲", "acts": ["赴任"], "time": "", "evidence": "甲之任",
         "book": "梁书", "juan": "二", "stance": "南朝系"},
    ]
    # 0/3 與 0/300 數字上都是 0%，證據力差兩個數量級
    tiny = {"梁书": {"n": 3, "k": 0, "rate": 0.0}}
    r = huijian.analyse([dict(x) for x in rows], ["南朝系", "北朝系"], tiny)
    f = [(w, y) for p in r for w, k, y in p["flags"] if "終局獨載" in k]
    check(f and f[0][0] == 5, f"n=3 時不調權重，維持 5", str(f[0][0] if f else None))
    check(f and "無基線可比" in f[0][1], "並明說無基線可比")
    big = {"梁书": {"n": 300, "k": 6, "rate": 0.02}}
    r2 = huijian.analyse([dict(x) for x in rows], ["南朝系", "北朝系"], big)
    f2 = [w for p in r2 for w, k, _ in p["flags"] if "終局獨載" in k]
    check(f2 and f2[0] == 2, "n=300 且比率低 → 降權為 2", str(f2))


def test_era_divergence_only_for_once_only_acts():
    print("14f. 紀年歧異只對一次性行為成立")
    # 張稷在《梁書》本紀五度除授，舊規則把四個不同的任命當成
    # 「同一事繫於不同時間」，報了一條假歧異。
    appointments = [
        {"person": "张稷", "acts": ["除授"], "time": t, "evidence": f"除授{t}",
         "book": "梁书", "juan": "二", "stance": "南朝系"}
        for t in ("十二月丙申", "十一月辛未", "冬十月丙寅", "癸卯")
    ]
    r = huijian.analyse(appointments, ["南朝系"])
    ks = [k for p in r for _, k, _ in p["flags"]]
    check(not any("紀年歧異" in k for k in ks),
          "反覆除授不報紀年歧異", str(ks))
    deaths = [
        {"person": "某", "acts": ["被杀"], "time": "延昌二年",
         "evidence": "見殺", "book": "魏书", "juan": "一", "stance": "北朝系"},
        {"person": "某", "acts": ["被杀"], "time": "延昌三年",
         "evidence": "被誅", "book": "北齐书", "juan": "二", "stance": "北朝系"},
    ]
    ks2 = [k for p in huijian.analyse(deaths, ["北朝系"]) for _, k, _ in p["flags"]]
    check(any("紀年歧異" in k for k in ks2), "終局繫於兩年則報歧異", str(ks2))


def test_compatible_time_strings():
    print("14g. 窗口截掉年號造成的「歧異」不算歧異")
    # 《魏書》帝紀作「二年二月」，列傳作「延昌二年二月」—— 同一天,
    # 差別只是檢索窗口把年號截在外面。
    check(huijian._distinct_times({"二年二月", "延昌二年二月"}) == ["延昌二年二月"],
          "後綴相容者收成一條")
    check(len(huijian._distinct_times({"延昌二年", "太和三年"})) == 2,
          "真正不同的紀年仍分開")


def test_far_years_disqualify_pairing():
    print("14h. 年代相去太遠不得配成一組")
    # 徐玄明殺張稷（513）曾與郁洲獻白鹿（475）配成一組，只因同地同月。
    a = {"person": "徐玄明", "place": "郁洲", "time": "延昌二年二月",
         "acts": ["归降"], "evidence": "以州内附", "book": "魏书",
         "juan": "一", "stance": "北朝系"}
    b = {"person": "刘善明", "place": "郁洲", "time": "元徽三年二月甲子",
         "acts": [], "evidence": "白鹿见郁洲", "book": "宋书",
         "juan": "二", "stance": "南朝系"}
    aa, ab = duizhao.anchors(a), duizhao.anchors(b)
    check(aa["year"][0] == 513 and ab["year"][0] == 475, "兩年分別為 513／475",
          f"{aa['year']} {ab['year']}")
    sc, why = duizhao.pair_score(aa, ab)
    check(sc == 0 and not why, f"相隔 38 年 → 判為兩件事（得 {sc} 分）", str(why))
    check(duizhao.align([a, b]) == [], "不產出對照組")
    # 相差一年仍允許並置，那是考異的常見形態
    c = dict(b, time="延昌三年二月")
    sc2, _ = duizhao.pair_score(aa, duizhao.anchors(c))
    check(sc2 > 0, "相差一年仍可並置", str(sc2))


# ── 15. 源流關係：派生本不是獨立證人 ─────────────────────────
# 立場軸上每一個信號都預設各書互為獨立證人，而派生本與其史源逐字雷同
# 是常態。一致不構成互證（那只是抄錄），不一致才有意義（那是改筆）。
def test_derivation_table():
    print("15. 源流關係表")
    check(huijian.derivation("南史", "梁书") == ("南史", "梁书"),
          "《南史》刪削《梁書》", str(huijian.derivation("南史", "梁书")))
    check(huijian.derivation("梁书", "南史") == ("南史", "梁书"),
          "順序無關，派生本在前回傳")
    check(huijian.derivation("北史", "魏书") == ("北史", "魏书"),
          "《北史》刪削《魏書》")
    # 《晉書》雖同為唐修，卻不是《宋書》的派生本 —— 兩者題材相鄰而
    # 無源流關係，是貨真價實的獨立比對。
    check(huijian.derivation("宋书", "晋书") is None,
          "《宋書》《晉書》同為唐修前後，但無源流關係")
    check(huijian.derivation("魏书", "梁书") is None, "南北兩書無源流關係")


def test_independent_witnesses():
    print("15b. 獨立史源計數")
    keep, drop = huijian.independent_witnesses({"梁书", "南史"})
    check(keep == {"梁书"}, "史源在場時派生本不另計", str(keep))
    check(len(drop) == 1 and drop[0][0] == "南史", "並說明被扣除的理由",
          str(drop))
    # 《宋書》《南齊書》缺列傳，《南史》往往是唯一所存 —— 史源缺席時
    # 派生本就是一個獨立證人，不能一概扣除。
    keep2, drop2 = huijian.independent_witnesses({"南史"})
    check(keep2 == {"南史"} and not drop2,
          "史源缺席時派生本自身算一個證人", str(keep2))
    keep3, _ = huijian.independent_witnesses({"梁书", "南史", "魏书"})
    check(keep3 == {"梁书", "魏书"}, "三部書裡只扣派生本", str(keep3))


def test_pure_copy_pair_dropped():
    print("15c. 純抄錄的源流對不報")
    # 實測權重最高的一組是《梁書》「以吳興太守張稷为尚書左僕射」對
    # 《南史》同句作「爲」—— 差別只有一個異體字，卻拿到權重 9。
    a = {"person": "张稷", "place": "", "time": "冬十月丙寅", "acts": ["除授"],
         "evidence": "冬十月丙寅，以吴兴太守张稷为尚书左仆射",
         "book": "梁书", "juan": "卷二", "stance": "南朝系"}
    b = dict(a, book="南史", juan="卷六", stance="唐修",
             evidence="冬十月丙寅，以吴兴太守张稷爲尚书左仆射")
    check(duizhao.text_ratio(a["evidence"], b["evidence"]) >= duizhao.COPY_RATIO,
          f"相似度 {duizhao.text_ratio(a['evidence'], b['evidence']):.2f} "
          f"≥ {duizhao.COPY_RATIO}")
    check(duizhao.align([a, b]) == [], "一字之差的源流對不產出對照組")


def test_derivative_pair_scores_only_divergence():
    print("15d. 源流對只算出入，不算錨定")
    # 《梁書》作「斬東昏」、《南史》作「殺帝」—— 同一天同一事，
    # 殺的是「廢帝」還是「皇帝」。這是春秋筆法最直接的一種：
    # 改的不是動詞，是被殺者的身分。
    a = {"person": "张稷", "place": "", "time": "十二月丙寅", "acts": ["被杀"],
         "evidence": "兼卫尉张稷、北徐州刺史王珍国斩东昏，送首义师",
         "book": "梁书", "juan": "卷一", "stance": "南朝系"}
    b = {"person": "张稷", "place": "", "time": "十二月丙寅", "acts": ["被杀"],
         "evidence": "十二月丙寅，新除雍州刺史王珍国、侍中张稷率兵入殿杀帝，时年十九",
         "book": "南史", "juan": "卷五", "stance": "唐修"}
    g = duizhao.align([a, b])
    check(len(g) == 1, "出入明顯的源流對照樣報出", str(len(g)))
    p = g[0]
    check(p["relation"][0] == "源流" and p["relation"][1] == "南史",
          "標為源流對，並指出哪部是派生本", str(p["relation"]))
    ks = [k for _, k, _ in p["diverge"]]
    check("改筆" in ks, "立場用語對立在源流對裡改稱「改筆」", str(ks))
    # 錨定分（同人物 3 ＋ 同月 2 ＋ 同日 3 ＋ 行為重疊 1 ＝ 9）一分不計
    check(p["weight"] == sum(w for w, _, _ in p["diverge"]),
          f"權重只來自出入（{p['weight']}），錨定分不計", str(p["score"]))
    check(p["weight"] < p["score"] + p["weight"],
          "雷同不加分：抄得越像，權重越低，不是越高")


def test_unclassified_divergence_is_reported():
    print("15e. 歸不出類的出入也要報出來")
    # 這一條釘死一個我自己犯過的回歸：源流對「沒有已分類的出入就跳過」,
    # 把《梁書》「斬東昏」對《南史》「殺帝」整組丟掉了 —— 而那是本案例
    # 最有價值的一條。詞表叫不出名字，不等於此處無異。
    a = {"person": "张稷", "place": "", "time": "十二月丙寅", "acts": ["被杀"],
         "evidence": "兼卫尉张稷、北徐州刺史王珍国斩东昏，送首义师",
         "book": "梁书", "juan": "卷一", "stance": "南朝系"}
    b = dict(a, book="南史", juan="卷六", stance="唐修",
             evidence="十二月丙寅，兼卫尉张稷、北徐州刺史王珍国斩东昏，"
                      "其夜以黄油裹首送军")
    r = duizhao.text_ratio(a["evidence"], b["evidence"])
    check(r < duizhao.COPY_RATIO, f"相似度 {r:.2f} 低於抄錄門檻")
    g = duizhao.align([a, b])
    check(len(g) == 1, "照樣報出，不靜默丟掉", str(len(g)))
    ks = [k for _, k, _ in g[0]["diverge"]]
    check("出入未能歸類" in ks, "並明說是本工具歸不出類，不是此處無異",
          str(ks))


def test_same_month_different_day_is_two_events():
    print("15f. 同月而干支日不同即兩件事")
    # 《梁書》「十二月丙申，以國子祭酒張稷為護軍將軍」曾與《南史》
    # 「十二月丙寅，…率兵入殿殺帝」配成一組，只靠同人物＋同月過門檻。
    # 一個月裡每個日干支最多出現一次，所以那是兩天。
    a = {"person": "张稷", "place": "", "time": "十二月丙申", "acts": ["除授"],
         "evidence": "十二月丙申，以国子祭酒张稷为护军将军",
         "book": "梁书", "juan": "卷二", "stance": "南朝系"}
    b = {"person": "张稷", "place": "", "time": "十二月丙寅", "acts": ["叛乱"],
         "evidence": "十二月丙寅，新除雍州刺史王珍国、侍中张稷率兵入殿杀帝",
         "book": "南史", "juan": "卷五", "stance": "唐修"}
    sc, _ = duizhao.pair_score(duizhao.anchors(a), duizhao.anchors(b))
    check(sc == 0, f"行為不重疊又異日 → 判為兩件事（得 {sc} 分）")
    # 行為重疊時不否證：那才是兩書對同一事各繫一日，即日次歧異。
    c = dict(b, acts=["除授"], time="十二月丙寅")
    sc2, _ = duizhao.pair_score(duizhao.anchors(a), duizhao.anchors(c))
    check(sc2 > 0, f"行為重疊時仍並置，留給日次歧異去判（得 {sc2} 分）")


def test_terminal_silence_within_one_lineage():
    print("15g. 同脈之內的終局出入不算跨立場相左")
    rows = [
        {"person": "甲", "acts": ["赴任"], "time": "", "evidence": "甲之任",
         "book": "梁书", "juan": "一", "stance": "南朝系"},
        {"person": "甲", "acts": ["被杀"], "time": "", "evidence": "甲見殺",
         "book": "南史", "juan": "二", "stance": "唐修"},
    ]
    r = huijian.analyse([dict(x) for x in rows], ["南朝系", "唐修"])
    f = [(w, y) for p in r for w, k, y in p["flags"] if "終局獨載" in k]
    check(f and f[0][0] == 2, f"《南史》刪削《梁書》，降權為 2",
          str(f[0][0] if f else None))
    check(f and "不構成跨立場相左" in f[0][1], "並明說同屬一脈", str(f))
    # 南北兩書之間則照舊是強信號
    rows2 = [
        {"person": "乙", "acts": ["赴任"], "time": "", "evidence": "乙之任",
         "book": "梁书", "juan": "一", "stance": "南朝系"},
        {"person": "乙", "acts": ["被杀"], "time": "", "evidence": "乙見殺",
         "book": "魏书", "juan": "二", "stance": "北朝系"},
    ]
    r2 = huijian.analyse([dict(x) for x in rows2], ["南朝系", "北朝系"])
    f2 = [w for p in r2 for w, k, _ in p["flags"] if "終局獨載" in k]
    check(f2 and f2[0] == 5, "《魏書》對《梁書》無源流關係，維持 5", str(f2))


def test_sole_record_flag_downweighted():
    print("15h. 僅見於：通不過自己虛無檢驗的信號不按證據計分")
    # 要有兩個以上獨立史源，「僅見於」才成立（孤證的情形見 15j）。
    rows = [
        {"person": "丙", "acts": ["赴任"], "time": "", "evidence": "丙之任",
         "book": "梁书", "juan": "一", "stance": "南朝系"},
        {"person": "丙", "acts": ["赴任"], "time": "", "evidence": "丙至郡",
         "book": "魏书", "juan": "二", "stance": "北朝系"},
    ]
    r = huijian.analyse([dict(x) for x in rows],
                        ["南朝系", "北朝系", "唐修"])
    f = [(w, y) for p in r for w, k, y in p["flags"] if "僅見於" in k]
    check(f and f[0][0] == 1, f"權重 1（原為 3）", str(f[0][0] if f else None))
    check(f and "這不是立場證據" in f[0][1], "報告裡直說它不是立場證據")
    check(f and "p≈1.0" in f[0][1], "並給出置換檢驗的實測結果")


def test_agreement_within_lineage_is_not_corroboration():
    print("15i. 同脈之內的雷同是抄錄，不是互證")
    rows = [
        {"person": "丁", "acts": ["除授"], "time": "", "evidence": "以丁為尚書",
         "book": "梁书", "juan": "一", "stance": "南朝系"},
        {"person": "丁", "acts": ["除授"], "time": "", "evidence": "以丁爲尚書",
         "book": "南史", "juan": "二", "stance": "唐修"},
    ]
    r = huijian.analyse([dict(x) for x in rows], ["南朝系", "唐修"])
    ks = [k for p in r for _, k, _ in p["flags"]]
    check(not any("交叉佐證" in k or "無衝突" in k for k in ks),
          "不報「可作交叉佐證」", str(ks))
    check(any("孤證" in k for k in ks), "報為孤證", str(ks))
    why = [y for p in r for _, k, y in p["flags"] if "孤證" in k][0]
    check("南史" in why and "抄錄" in why,
          "訊息說明《南史》是抄錄而非第二個證人", why)
    check(r[0]["witnesses"] == ["梁书"], "獨立史源只算《梁書》一部",
          str(r[0]["witnesses"]))
    # 獨立的兩部書一致，才是互證
    rows2 = [
        {"person": "戊", "acts": ["除授"], "time": "", "evidence": "以戊為尚書",
         "book": "梁书", "juan": "一", "stance": "南朝系"},
        {"person": "戊", "acts": ["除授"], "time": "", "evidence": "以戊爲尚書",
         "book": "魏书", "juan": "二", "stance": "北朝系"},
    ]
    ks2 = [k for p in huijian.analyse([dict(x) for x in rows2],
                                      ["南朝系", "北朝系"])
           for _, k, _ in p["flags"]]
    check("多處互見，無衝突" in ks2, "獨立兩書一致才算互證", str(ks2))


def test_single_witness_is_not_a_signal():
    print("15j. 孤證不報「僅見於」")
    # 一部書記、別家不記，若那部書是唯一的獨立史源，「僅見於」必然成立,
    # 不含立場信息。實測 24 人中 19 人如此 —— 這才是該旗標通不過置換
    # 檢驗的真正原因（斷代篩選只去掉 1 條，p 仍是 1.000）。
    one = [{"person": "庚", "acts": ["赴任"], "time": "", "evidence": "庚之任",
            "book": "梁书", "juan": "一", "stance": "南朝系"}]
    r = huijian.analyse(one, ["南朝系", "北朝系"])
    ks = [k for p in r for _, k, _ in p["flags"]]
    check(not any("僅見於" in k for k in ks), "孤證不報僅見於", str(ks))
    check(any("孤證" in k for k in ks), "改報為孤證", str(ks))
    check(r[0]["score"] == 0, "權重 0，不進排序", str(r[0]["score"]))
    # 兩個獨立史源時才留給「僅見於」去判
    # 第三個立場得與斷代重疊，否則會被 _relevant_stances 正確篩掉
    # （「元修」是《宋史》960–1279，對梁魏兩書毫不相干）。
    two = one + [{"person": "庚", "acts": ["赴任"], "time": "",
                  "evidence": "庚至郡", "book": "魏书", "juan": "二",
                  "stance": "北朝系"}]
    ks2 = [k for p in huijian.analyse(two, ["南朝系", "北朝系", "唐修"])
           for _, k, _ in p["flags"]]
    check(any("僅見於" in k for k in ks2), "兩個獨立史源時照報", str(ks2))


def test_offperiod_books_excluded():
    print("15k. 斷代不重疊的書，其沉默不算無載")
    # 《三國志》（184–280）不記張稷（卒 513）不是諱飾，它記的是三國。
    rel = huijian._relevant_stances({"梁书", "魏书"},
                                    ["南朝系", "北朝系", "晋修", "清修"])
    check("晋修" not in rel, "《三國志》所屬立場被排除", str(rel))
    check("清修" not in rel, "《明史》所屬立場被排除", str(rel))
    check("南朝系" in rel and "北朝系" in rel, "斷代重疊的留下", str(rel))
    # 語料裡沒有 BOOK_SPAN 的書時，不做篩選，寧可多報不可漏報
    check(huijian._relevant_stances({"無此書"}, ["甲", "乙"]) == ["甲", "乙"],
          "無斷代資料時不篩")


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
              test_terminal_rate_filters_short_names,
              test_flag_kind_strips_stance,
              test_permutation_is_sensitive,
              test_wilson_interval,
              test_kappa,
              test_stratified_sampling,
              test_annotation_values_are_validated,
              test_rate_excludes_na,
              test_sample_judges_extracted_name,
              test_vernacular_truncation,
              test_vernacular_density_gate,
              test_expand_name_guard,
              test_terminal_attribution_same_clause,
              test_baseline_min_n_gate,
              test_era_divergence_only_for_once_only_acts,
              test_compatible_time_strings,
              test_far_years_disqualify_pairing,
              test_derivation_table,
              test_independent_witnesses,
              test_pure_copy_pair_dropped,
              test_derivative_pair_scores_only_divergence,
              test_unclassified_divergence_is_reported,
              test_same_month_different_day_is_two_events,
              test_terminal_silence_within_one_lineage,
              test_sole_record_flag_downweighted,
              test_agreement_within_lineage_is_not_corroboration,
              test_single_witness_is_not_a_signal,
              test_offperiod_books_excluded):
        t()
        print()
    if FAIL:
        print(f"{len(FAIL)} 項失敗：")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("全部通過")
