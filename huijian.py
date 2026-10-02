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

import argparse, html, json, os, pathlib, re, subprocess, sys, urllib.request

REPO = "https://github.com/hunterhug/china-history.git"
RAW = pathlib.Path("china-history")
COR = pathlib.Path("corpus")
MODEL = "claude-sonnet-4-6"

# ── 立場表：決定「單方獨載」怎麼算 ────────────────────────────
STANCE = {
    "宋书": "南朝系", "南齐书": "南朝系", "梁书": "南朝系",
    "陈书": "南朝系", "南史": "南朝系",
    "魏书": "北朝系", "北齐书": "北朝系", "周书": "北朝系", "北史": "北朝系",
    "晋书": "唐修", "隋书": "唐修",
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


def scan(terms, window=200):
    """回傳 [(書, 卷, 立場, 命中詞, 上下文)]，去重"""
    seen, hits = set(), []
    for f in sorted(COR.rglob("*.txt")):
        if any(w in f.name for w in ("译文", "譯文", "白话", "白話", "段译")):
            continue
        book = f.parts[1]
        s = f.read_text(encoding="utf-8")
        for t in terms:
            for m in re.finditer(re.escape(t), s):
                a, b = max(0, m.start() - window), min(len(s), m.end() + window)
                ctx = re.sub(r"\s+", "", s[a:b])
                k = ctx[:45]
                if k in seen:
                    continue
                seen.add(k)
                juan = re.sub(r"-原文|第.+?章-|原文版|段译", "", f.stem)
                hits.append((book, juan, STANCE.get(book, "?"), t, ctx))
    return hits


def cmd_search(a):
    terms = variants(a.term, a.also or ())
    print(f"檢索詞: {'、'.join(terms)}\n", file=sys.stderr)
    hits = scan(terms, a.window)
    by_stance = {}
    for h in hits:
        by_stance.setdefault(h[2], []).append(h)
    for st, rows in sorted(by_stance.items(), key=lambda x: -len(x[1])):
        print(f"\n═══ {st} · {len(rows)} 段 ═══")
        for bk, juan, _, t, ctx in rows:
            print(f"\n【{bk}·{juan}】({t})\n …{ctx}…")
    print(f"\n共 {len(hits)} 段", file=sys.stderr)


def cmd_dossier(a):
    terms = variants(a.term, a.also or ())
    hits = scan(terms, a.window)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    groups = {}
    for h in hits:
        groups.setdefault(h[2], []).append(h)
    for st, rows in groups.items():
        p = out / f"{a.term}_{st}.txt"
        with p.open("w", encoding="utf-8") as fh:
            for bk, juan, _, _, ctx in rows:
                fh.write(f"《{bk}·{juan}》\n…{ctx}…\n\n")
        print(f"{p}  {len(rows)} 段  {sum(len(r[4]) for r in rows):,} 字")


# ── 模型層：只做抽取 ───────────────────────────────────────
EXTRACT_PROMPT = """你是史料抽取器。只抽取，不判断，不推论。

铁律：
1. 只记录原文明确写了的。不得补全、演绎、据常识推断。
2. evidence 必须是原文中逐字连续的片段，一字不改。
3. 无可抽者返回 []。宁可空，不可凑。
4. person 用原文出现的写法，不要自行补全姓氏。

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
    """回傳 (rows, 攔截數)。逐字校驗是防杜撰的唯一硬保證。"""
    rows, dropped, calls = [], 0, 0
    groups = {}
    for bk, juan, st, _, ctx in hits:
        groups.setdefault((bk, juan, st), []).append(ctx)
    for i, ((bk, juan, st), ctxs) in enumerate(groups.items(), 1):
        src = "\n".join(dict.fromkeys(ctxs))
        for ck in chunks(src):
            if limit and calls >= limit:
                print(f"  達到 --limit {limit}，停止呼叫", file=sys.stderr)
                return rows, dropped
            calls += 1
            print(f"  [{i}/{len(groups)}] {bk}·{juan}  ({len(ck)}字)", file=sys.stderr)
            if dry:
                continue
            try:
                items = call_api(EXTRACT_PROMPT.format(acts="、".join(ACTS), chunk=ck), key)
            except Exception as e:
                print(f"    ! API 失敗: {e}", file=sys.stderr)
                continue
            if debug:
                print(f"    模型返回 {len(items)} 條", file=sys.stderr)
            for it in items:
                ev = (it.get("evidence") or "").strip()
                if len(ev) >= 4 and ev in ck:          # ← 逐字校驗
                    it["evidence"] = ev
                    it.update(book=bk, juan=juan, stance=st)
                    if it.get("person"):
                        rows.append(it)
                else:
                    dropped += 1
                    if debug:
                        print(f"    ✗ 攔截: 「{ev[:30]}」不在原文", file=sys.stderr)
    return rows, dropped


# ── 確定性層：矛盾 / 歧異 / 獨載 ──────────────────────────
def analyse(rows, all_stances):
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

        # 終局獨載：兩系都記其人，卻只有一系記其死 —— 最強的諱飾信號
        if len(stances) > 1 and term:
            for a in term:
                who = sorted({h["stance"] for h in hits
                              if a in h.get("acts", [])})
                silent = [s for s in stances if s not in who]
                if silent:
                    flags.append((5, f"終局獨載（{a}）：僅「{'、'.join(who)}」有載",
                                  f"「{'、'.join(silent)}」記其人而不記其終。"
                                  f"一方詳其死、另一方諱其死，曲筆之典型。"))

        missing = [s for s in all_stances if s not in stances]
        if len(all_stances) > 1 and missing:
            flags.append((3, f"僅見於「{'、'.join(stances)}」",
                          f"「{'、'.join(missing)}」無載。可能是諱飾，也可能該書本不記此類事，"
                          f"或材料散佚。需比對同類人物的正常著錄率再判斷。"))

        if len(hits) > 1 and not flags:
            flags.append((1, "多處互見，無衝突", "記載彼此一致，可作交叉佐證。"))

        out.append({"person": name, "stances": stances, "acts": acts,
                    "flags": flags, "hits": hits,
                    "score": sum(w for w, _, _ in flags)})
    return sorted(out, key=lambda x: -x["score"])


def report(res, dropped, fh=sys.stdout):
    print(f"\n{'='*64}\n人物 {len(res)}　攔下偽引 {dropped} 條\n{'='*64}", file=fh)
    for p in res:
        print(f"\n■ {p['person']}　[{'|'.join(p['stances'])}]　權重 {p['score']}", file=fh)
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
    res = analyse(rows, sorted({h[2] for h in hits}))
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    json.dump(res, (out / f"{a.term}_findings.json").open("w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with (out / f"{a.term}_report.txt").open("w", encoding="utf-8") as fh:
        report(res, dropped, fh)
    report(res, dropped)
    print(f"\n→ {out}/{a.term}_findings.json（可導入 React demo 人工核定）", file=sys.stderr)



# ══ Agent 模式：不需要 API key，由 Claude Code 充當 L2 抽取層 ══

def _mkchunks(hits):
    groups = {}
    for bk, juan, st, _, ctx in hits:
        groups.setdefault((bk, juan, st), []).append(ctx)
    out = []
    for (bk, juan, st), cs in groups.items():
        src = "\n".join(dict.fromkeys(cs))
        for ck in chunks(src):
            out.append({"ci": len(out), "book": bk, "juan": juan,
                        "stance": st, "chunk": ck})
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
            if len(ev) >= 4 and ev in c["chunk"]:
                rows.append({**it, "evidence": ev, "book": c["book"],
                             "juan": c["juan"], "stance": c["stance"]})
            else:
                dropped.append({**it, "why": "引文不在原文"})
    rows = [r for r in rows if r.get("person")]
    if not rows:
        sys.exit("無有效抽取結果")

    seen, uniq = set(), []
    for r in rows:                       # 兩階段會重複抽到同一段
        k = (r["person"], r["evidence"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)

    res = analyse(uniq, sorted({r["stance"] for r in uniq}))
    _dump(w / "findings.json", res)
    if dropped:
        print(f"攔下 {len(dropped)} 條偽引：", file=sys.stderr)
        for d in dropped[:10]:
            print(f"  ✗ {d.get('person','?')}「{(d.get('evidence') or '')[:26]}」 {d['why']}",
                  file=sys.stderr)
    with (w / "report.txt").open("w", encoding="utf-8") as fh:
        report(res, len(dropped), fh)
    report(res, len(dropped))
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
    for nm, fn in [("expand", cmd_expand), ("verify", cmd_verify)]:
        q = sub.add_parser(nm)
        q.add_argument("out", help="工作目錄")
        q.add_argument("--window", type=int, default=200)
        q.set_defaults(fn=fn)

    a = ap.parse_args()
    if a.cmd != "fetch" and not COR.exists():
        sys.exit("先跑 python huijian.py fetch")
    a.fn(a)


if __name__ == "__main__":
    main()
