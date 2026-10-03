#!/usr/bin/env python3
"""回歸測試：三處已修缺陷。

不需要語料，不需要 API key。直接跑：

    python tests/test_fixes.py
"""

import os, pathlib, shutil, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import huijian, duizhao

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


if __name__ == "__main__":
    for t in (test_scan_keeps_parallel_passages,
              test_scan_still_folds_duplicates_within_a_book,
              test_era_disambiguation,
              test_no_phantom_year_anchor, test_stance_of_derivative_histories,
              test_evidence_gate,
              test_offset_anchoring_rejects_splice,
              test_person_normalization,
              test_ambiguous_alias_not_suggested):
        t()
        print()
    if FAIL:
        print(f"{len(FAIL)} 項失敗：")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("全部通過")
