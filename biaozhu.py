#!/usr/bin/env python3
"""
標注 biaozhu — 抽樣、標注、計分

把「抽取精度未測」變成一個帶信賴區間的數字。

照文檔 05 的兩輪流程做：第一輪 30 條兩人獨立標，比 κ、改指南；
第二輪 150–200 條，算精度。本模組只管抽樣與計分，判斷仍然由人下。

    python biaozhu.py sample work/ -n 30          # 第一輪：預試
    python biaozhu.py sample work/ -n 150         # 第二輪：正式
    python biaozhu.py score work/                 # 計分
    python biaozhu.py score work/ --second 乙.json # 兩人一致性 κ

三條軌道分開計分，因為它們的可標注性根本不同：

  A 抽取精度   問「原文是否支持這條記載」。可核驗，不是解釋。
               兩個細心的讀者幾乎總能一致。這一軌出頭條數字。
  B 信號效度   問「這個旗標是否成立」。分兩層：讀得對不對（可核驗）、
               諱飾是否最佳解釋（解釋性，預期一致性低）。
  C 負對照     問「攔截得對不對」。攔截率一直有人報，攔得對不對沒人量過。
               誤攔是召回的淨損失，不量就不知道閘門的代價。
"""

import argparse, collections, json, math, pathlib, random, re, sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# 各軌允許的判斷值。score 遇到不在表內的值會報錯而不是悄悄略過 ——
# 標注檔是手寫的，拼錯在所難免，默默跳過會讓分母無聲縮水。
VALUES = {
    "A": {"person": ["ok", "wrong", "unsupported", "na"],
          "acts": ["ok", "wrong", "unsupported", "na"],
          "time": ["ok", "wrong", "unsupported", "na"],
          "place": ["ok", "wrong", "unsupported", "na"],
          "overall": ["ok", "wrong"]},
    "B": {"reading": ["ok", "wrong"],
          "interpretation": ["yes", "no", "undecidable"]},
    "C": {"rejection": ["correct", "false_reject"]},
}


# ── 統計 ──────────────────────────────────────────────────
def wilson(k, n, z=1.96):
    """Wilson 區間。小樣本下比常態近似可靠，k=0 或 k=n 時也不退化。"""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def kappa(pairs):
    """Cohen's κ。pairs 為 [(甲判, 乙判)]。

    文檔 05 說得對：κ 有雙重作用 —— 測標注質量，也測這個維度本身可不可做。
    κ < 0.4 時不該去調標注指南，該考慮砍掉這個維度。
    """
    if not pairs:
        return None
    cats = sorted({v for p in pairs for v in p})
    n = len(pairs)
    po = sum(1 for a, b in pairs if a == b) / n
    ca = collections.Counter(a for a, _ in pairs)
    cb = collections.Counter(b for _, b in pairs)
    pe = sum((ca[c] / n) * (cb[c] / n) for c in cats)
    if abs(1 - pe) < 1e-12:
        return None                    # 兩人都只用一個類別，κ 無定義
    return (po - pe) / (1 - pe)


def stratified(pool, n, key, seed=0):
    """分層抽樣，按層的大小比例分配，餘數給小數部分最大的層。

    分層是為了讓罕見的那一類也進得了樣本：純隨機抽 150 條，
    只出現三次的旗標很可能一條都抽不到，那一類就永遠沒有數字。
    """
    pool = list(pool)
    if n >= len(pool):
        return pool
    groups = {}
    for it in pool:
        groups.setdefault(key(it), []).append(it)
    rnd = random.Random(seed)
    for g in groups.values():
        rnd.shuffle(g)
    total, alloc, frac = len(pool), {}, []
    for k, g in groups.items():
        exact = n * len(g) / total
        alloc[k] = min(len(g), int(exact))
        frac.append((exact - int(exact), str(k)))
    short = n - sum(alloc.values())
    order = {str(k): k for k in groups}
    for _, ks in sorted(frac, reverse=True):
        if short <= 0:
            break
        k = order[ks]
        if alloc[k] < len(groups[k]):
            alloc[k] += 1
            short -= 1
    out = []
    for k in sorted(groups, key=str):
        out += groups[k][:alloc[k]]
    return out


# ── 抽樣 ──────────────────────────────────────────────────
def _flag_kind(k):
    k = re.sub(r"「[^」]*」", "", k)
    return re.split(r"[：（(]", k)[0].strip() or "其他"


def _load(w, fn):
    p = w / fn
    return json.load(open(p, encoding="utf-8")) if p.exists() else []


def build_sample(w, n_a, n_b, n_c, seed, terminal):
    res = _load(w, "findings.json")
    if not res:
        sys.exit(f"找不到 {w}/findings.json（先跑 huijian.py verify）")

    # A 軌：抽取記載。分層＝(立場, 是否含終局行為) —— 終局那類是信號的來源，
    # 必須單獨保證樣本量，否則最重要的一類反而測不出數字。
    rows = []
    for p in res:
        for h in p.get("hits") or []:
            rows.append({**h, "_person_group": p["person"]})
    a_key = lambda r: (r.get("stance", "?"),
                       bool(set(r.get("acts") or []) & terminal))
    a_pick = stratified(rows, n_a, a_key, seed)

    # B 軌：旗標。分層＝旗標類別
    flags = []
    for p in res:
        for f in p.get("flags") or []:
            wt, kk, why = f[0], f[1], f[2]
            flags.append({"person": p["person"], "weight": wt, "flag": kk,
                          "why": why, "kind": _flag_kind(kk),
                          "stances": p.get("stances"),
                          "hits": [{"book": h.get("book"), "juan": h.get("juan"),
                                    "stance": h.get("stance"),
                                    "time": h.get("time"),
                                    "acts": h.get("acts"),
                                    "evidence": h.get("evidence")}
                                   for h in (p.get("hits") or [])]})
    b_pick = stratified(flags, n_b, lambda f: f["kind"], seed)

    # C 軌：負對照 —— 被閘門攔下的條目。分層＝攔截理由
    drops = _load(w, "dropped.json")
    c_pick = stratified(drops, n_c, lambda d: d.get("why", "?"), seed)

    items = []
    for i, r in enumerate(a_pick, 1):
        items.append({
            "id": f"A{i:04d}", "track": "A",
            "book": r.get("book"), "juan": r.get("juan"),
            "stance": r.get("stance"),
            "addr": [r.get("ev_start"), r.get("ev_end")],
            "src": r.get("src"),
            # A 軌測的是抽取，所以要判原始抽取的寫法。歸一是之後另一步、
            # 且由人確認過的，不該當成抽取錯誤扣在模型頭上。
            "person": r.get("person_raw") or r.get("person"),
            "person_normalized": r.get("person"),
            "title": r.get("title"), "place": r.get("place"),
            "time": r.get("time"), "acts": r.get("acts"),
            "evidence": r.get("evidence"),
            "verdict": {k: None for k in VALUES["A"]},
            "note": "",
        })
    for i, f in enumerate(b_pick, 1):
        items.append({
            "id": f"B{i:04d}", "track": "B",
            "person": f["person"], "kind": f["kind"], "flag": f["flag"],
            "weight": f["weight"], "why": f["why"],
            "stances": f["stances"], "hits": f["hits"],
            "verdict": {k: None for k in VALUES["B"]},
            "note": "",
        })
    for i, d in enumerate(c_pick, 1):
        ev = d.get("evidence") or ""
        ck = d.get("chunk") or ""
        items.append({
            "id": f"C{i:04d}", "track": "C",
            "why_rejected": d.get("why"),
            "book": d.get("book"), "juan": d.get("juan"),
            "person": d.get("person"), "evidence": ev,
            "evidence_in_chunk_substring": bool(ev) and ev in ck,
            "chunk": ck,
            "verdict": {k: None for k in VALUES["C"]},
            "note": "",
        })
    return items


GUIDE = {
    "A": ["person：evidence 是否支持這條記載講的就是此人？自行補全姓氏、認錯主語都算 wrong。",
          "  列出的 person 是**原始抽取**的寫法。若下面另標「歸一後作 X」，",
          "  那是人工確認過的合併結果，不在本軌的評判範圍內。",
          "acts：evidence 是否明確支持所報的每個行為？多報、推斷得來的算 wrong。",
          "time / place：同上。欄位為空時填 na，不要填 ok。",
          "overall：四欄只要有一個 wrong 或 unsupported，overall 就是 wrong。",
          "判斷只看 evidence 這一句，不要去回想史實 —— 測的是抽取，不是學識。"],
    "B": ["reading：這個旗標是否正確描述了下列記載所寫的內容？（可核驗）",
          "interpretation：諱飾／曲筆是否最佳解釋？yes / no / undecidable。",
          "  這一層是解釋性的，預期一致性低（動機類判斷專家間 κ 僅 0.27–0.32）。",
          "  覺得材料不足以判斷時就填 undecidable，不要勉強選邊 —— "
          "undecidable 的比例本身就是有用的結果。"],
    "C": ["rejection：這條被攔得對不對？",
          "  correct      = 引文確實不在原文（攔得對）",
          "  false_reject = 引文其實在原文裡，是閘門誤攔（召回的淨損失）",
          "evidence_in_chunk_substring 若為 true，表示它是 chunk 的子串但跨了接縫，"
          "多半是拼接 —— 仍請自行核對 chunk 再判。"],
}


def worksheet(items, fh):
    """給人讀的本子。判斷仍然填回 JSON，這份只為看得清楚。"""
    by = collections.defaultdict(list)
    for it in items:
        by[it["track"]].append(it)
    for tr in ("A", "B", "C"):
        if not by[tr]:
            continue
        name = {"A": "抽取精度", "B": "信號效度", "C": "負對照（攔截）"}[tr]
        print(f"\n{'='*72}\n{tr} 軌 · {name} · {len(by[tr])} 條\n{'='*72}", file=fh)
        for g in GUIDE[tr]:
            print(f"  {g}", file=fh)
        for it in by[tr]:
            print(f"\n── {it['id']} ──", file=fh)
            if tr == "A":
                print(f"《{it['book']}·{it['juan']}》[{it['stance']}]"
                      f" 位址 {it['addr']}", file=fh)
                print(f"  person={it['person']!r} title={it['title']!r}"
                      f" place={it['place']!r} time={it['time']!r}", file=fh)
                if it.get("person_normalized") != it.get("person"):
                    print(f"  （歸一後作 {it['person_normalized']!r}，"
                          f"不在本軌評判範圍）", file=fh)
                print(f"  acts={it['acts']}", file=fh)
                print(f"  「{it['evidence']}」", file=fh)
            elif tr == "B":
                print(f"{it['person']} [{'|'.join(it['stances'] or [])}]"
                      f" 權重 {it['weight']}", file=fh)
                print(f"  旗標：{it['flag']}", file=fh)
                print(f"  理由：{it['why']}", file=fh)
                for h in it["hits"]:
                    print(f"    〔{h['stance']}〕《{h['book']}·{h['juan']}》"
                          f" {h['time'] or '—'} {h['acts']}", file=fh)
                    print(f"      「{h['evidence']}」", file=fh)
            else:
                print(f"攔截理由：{it['why_rejected']}"
                      f"　是否為 chunk 子串：{it['evidence_in_chunk_substring']}",
                      file=fh)
                print(f"《{it['book']}·{it['juan']}》 person={it['person']!r}",
                      file=fh)
                print(f"  聲稱引文：「{it['evidence']}」", file=fh)
                print(f"  chunk：{it['chunk'][:300]}"
                      f"{'…' if len(it['chunk']) > 300 else ''}", file=fh)
            print(f"  判斷：{' / '.join(VALUES[tr])}", file=fh)


def cmd_sample(a):
    w = pathlib.Path(a.out)
    import huijian
    items = build_sample(w, a.n, a.flags, a.rejects, a.seed, huijian.TERMINAL)
    out = w / a.name
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(items, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    ws = out.with_suffix(".txt")
    with ws.open("w", encoding="utf-8") as fh:
        worksheet(items, fh)
    c = collections.Counter(i["track"] for i in items)
    print(f"抽出 {len(items)} 條：A {c['A']}　B {c['B']}　C {c['C']}")
    print(f"→ {out}（在此檔裡把 verdict 的 null 填掉）")
    print(f"→ {ws}（給人讀的本子）")
    if c["A"] < 100:
        print(f"\n注意：A 軌只有 {c['A']} 條。精度的信賴區間會很寬 —— "
              f"n=30 適合第一輪對指南，正式數字要 150 上下。")
    if not c["C"]:
        print("\n注意：沒有負對照（work/dropped.json 空或不存在）。"
              "閘門誤攔率將無從量測。")


# ── 計分 ──────────────────────────────────────────────────
def _check(items):
    bad = []
    for it in items:
        allowed = VALUES.get(it.get("track"), {})
        for k, v in (it.get("verdict") or {}).items():
            if v is None or k not in allowed:
                if k not in allowed:
                    bad.append(f"{it['id']}: 未知欄位 {k}")
                continue
            if v not in allowed[k]:
                bad.append(f"{it['id']}.{k}: 「{v}」不在 {allowed[k]}")
    return bad


def _rate(items, track, field, good):
    vs = [it["verdict"][field] for it in items
          if it.get("track") == track and (it.get("verdict") or {}).get(field)]
    vs = [v for v in vs if v != "na"]
    if not vs:
        return None
    k = sum(1 for v in vs if v in good)
    lo, hi = wilson(k, len(vs))
    return {"k": k, "n": len(vs), "p": k / len(vs), "lo": lo, "hi": hi}


def _line(label, r):
    if not r:
        return f"  {label:14s}  —  未標注"
    return (f"  {label:14s} {r['p']:6.1%}  "
            f"95% CI [{r['lo']:.1%}, {r['hi']:.1%}]   n={r['n']}")


def cmd_score(a):
    w = pathlib.Path(a.out)
    p = w / a.name
    if not p.exists():
        sys.exit(f"找不到 {p}")
    items = json.load(open(p, encoding="utf-8"))
    bad = _check(items)
    if bad:
        print("標注檔有問題，先修掉再計分：", file=sys.stderr)
        for b in bad[:20]:
            print("  ✗", b, file=sys.stderr)
        sys.exit(1)

    done = sum(1 for it in items
               if any(v is not None for v in (it.get("verdict") or {}).values()))
    print(f"標注檔 {p}　共 {len(items)} 條，已標 {done} 條")

    print(f"\n{'='*66}\nA 軌 · 抽取精度（可核驗）\n{'='*66}")
    for f in ("person", "acts", "time", "place"):
        print(_line(f, _rate(items, "A", f, {"ok"})))
    ov = _rate(items, "A", "overall", {"ok"})
    print(_line("overall", ov))
    if ov and ov["n"] < 100:
        print(f"  ⚠ n={ov['n']}，區間寬。這是預試數字，不是結論。")

    print(f"\n{'='*66}\nB 軌 · 信號效度\n{'='*66}")
    print(_line("reading", _rate(items, "B", "reading", {"ok"})))
    iv = [it["verdict"]["interpretation"] for it in items
          if it.get("track") == "B" and (it.get("verdict") or {}).get("interpretation")]
    if iv:
        c = collections.Counter(iv)
        tot = len(iv)
        print(f"  interpretation（解釋性，預期一致性低）n={tot}")
        for k in ("yes", "no", "undecidable"):
            print(f"      {k:12s} {c[k]:3d}  {c[k]/tot:5.1%}")
        if c["undecidable"] / tot > 0.4:
            print(f"  ⚠ undecidable 佔 {c['undecidable']/tot:.0%} —— "
                  f"材料多半不足以支撐這類判斷，別把它寫進結論。")
    else:
        print("  interpretation  —  未標注")

    # 按旗標類別分別看 reading
    kinds = collections.defaultdict(list)
    for it in items:
        if it.get("track") == "B" and (it.get("verdict") or {}).get("reading"):
            kinds[it.get("kind", "?")].append(it["verdict"]["reading"])
    if kinds:
        print("\n  各旗標類別的 reading 正確率：")
        for k, vs in sorted(kinds.items(), key=lambda x: -len(x[1])):
            ok = sum(1 for v in vs if v == "ok")
            lo, hi = wilson(ok, len(vs))
            print(f"      {k:12s} {ok/len(vs):6.1%}  "
                  f"[{lo:.0%}, {hi:.0%}]  n={len(vs)}")

    print(f"\n{'='*66}\nC 軌 · 負對照（閘門攔得對不對）\n{'='*66}")
    fr = _rate(items, "C", "rejection", {"false_reject"})
    if fr:
        print(_line("誤攔率", fr))
        print(f"  誤攔是召回的淨損失：{fr['k']}/{fr['n']} 條其實在原文裡。")
        if fr["p"] > 0.1:
            print("  ⚠ 誤攔超過一成，閘門或切塊方式該檢查了。")
    else:
        print("  —  未標注")

    # 兩人一致性
    if a.second:
        q = pathlib.Path(a.second)
        if not q.exists():
            q = w / a.second
        other = {it["id"]: it for it in json.load(open(q, encoding="utf-8"))}
        print(f"\n{'='*66}\n標注者一致性（Cohen's κ）　乙＝{q}\n{'='*66}")
        for tr, field in (("A", "overall"), ("B", "reading"),
                          ("B", "interpretation"), ("C", "rejection")):
            pairs = []
            for it in items:
                if it.get("track") != tr:
                    continue
                o = other.get(it["id"])
                va = (it.get("verdict") or {}).get(field)
                vb = (o.get("verdict") or {}).get(field) if o else None
                if va and vb:
                    pairs.append((va, vb))
            kp = kappa(pairs)
            if not pairs:
                print(f"  {tr}.{field:16s}  —  無共同標注")
                continue
            agree = sum(1 for x, y in pairs if x == y) / len(pairs)
            if kp is None:
                # 兩人都只用了一個類別：κ 無定義，但一致率仍有意義，
                # 不印出來會讓人誤以為什麼都沒測到。
                print(f"  {tr}.{field:16s} κ 無定義（兩人均只用單一類別）"
                      f"　一致 {agree:5.1%}  n={len(pairs)}")
                continue
            # 用四捨五入後的值判級，免得顯示 0.400 卻歸到「< 0.4」那一檔
            r = round(kp, 3)
            verdict = ("任務良定義" if r > 0.7 else
                       "可用但指南需收緊" if r >= 0.4 else
                       "連人都對不上 —— 考慮砍掉這個維度")
            print(f"  {tr}.{field:16s} κ={kp:6.3f}  一致 {agree:5.1%}  "
                  f"n={len(pairs)}　{verdict}")
        print("\n  參考（文檔 05）：κ > 0.7 良定義；κ < 0.4 不是改指南的問題，"
              "\n  是這個維度本身可能不可做。")

    print(f"\n{'='*66}")
    print("這些數字只覆蓋被標注的那部分，且是單人標注時的點估計。"
          "\n要能發表，還需兩人獨立標同一批並報 κ —— 流程見 docs/05。")


def main():
    ap = argparse.ArgumentParser(description="標注 — 抽樣與計分")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="分層抽樣，產出待標注檔")
    s.add_argument("out", help="工作目錄")
    s.add_argument("-n", type=int, default=150, help="A 軌條數，預設 150")
    s.add_argument("--flags", type=int, default=40, help="B 軌條數，預設 40")
    s.add_argument("--rejects", type=int, default=30, help="C 軌條數，預設 30")
    s.add_argument("--seed", type=int, default=0, help="隨機種子，便於復現")
    s.add_argument("--name", default="annot_sample.json", help="輸出檔名")
    s.set_defaults(fn=cmd_sample)

    c = sub.add_parser("score", help="讀回標注檔計分")
    c.add_argument("out", help="工作目錄")
    c.add_argument("--name", default="annot_sample.json", help="標注檔名")
    c.add_argument("--second", help="第二位標注者的檔案，給了就算 κ")
    c.set_defaults(fn=cmd_score)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
