import React, { useState } from "react";
import { Play, Plus, X, Check, HelpCircle, Ban, Download, ShieldCheck, AlertTriangle, Users } from "lucide-react";

/* ── 朱墨套印 ─────────────────────────────────── */
const C = {
  ink: "#13151B", panel: "#1B1E27", line: "#2B303E", paper: "#EAE5D9",
  zhu: "#D8483A", qing: "#4A80AE", gold: "#B08A44", mute: "#848B99", text: "#D5D8DF",
};
const SERIF = "'Songti SC','Noto Serif CJK SC','Source Han Serif SC','SimSun',serif";

/* ── 行为类型 ─────────────────────────────────── */
const ACTS = [
  "除授", "罢黜", "赴任", "征战", "战胜", "战败",
  "死于战", "病卒", "被杀", "归降", "被俘", "叛乱",
  "筑城", "赈济", "上书", "出使", "逃亡", "受封赏",
];

/* 终局互斥集：任两者并存即为硬矛盾 */
const TERMINAL = ["死于战", "病卒", "被杀", "归降", "被俘"];
const PAIRS = [["战胜", "战败"], ["除授", "罢黜"]];

/* ── 模型层：只做抽取 ─────────────────────────── */
async function callModel(prompt) {
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: "claude-sonnet-4-6", max_tokens: 1000, messages: [{ role: "user", content: prompt }] }),
  });
  const d = await r.json();
  const raw = d.content.filter(b => b.type === "text").map(b => b.text).join("");
  const s = raw.replace(/```json|```/g, "").trim();
  const a = s.indexOf("["), b = s.lastIndexOf("]");
  if (a < 0 || b < 0) return [];
  return JSON.parse(s.slice(a, b + 1));
}

const extractPrompt = (t) => `你是史料抽取器。只抽取，不判断，不推论。

铁律：
1. 只记录原文明确写了的。不得补全、演绎、据常识推断。
2. evidence 必须是原文中逐字连续的片段，一字不改。
3. 无可抽者返回 []。宁可空，不可凑。
4. person 用原文出现的写法（"善明"就写"善明"，不要自行补成"刘善明"）。

acts 只能选自：${ACTS.join("、")}

只输出 JSON 数组，无 markdown，无解释：
[{"person":"","title":"","place":"","time":"","acts":[],"evidence":""}]

史料：
<<<${t}>>>`;

const aliasPrompt = (names, ctx) => `以下是从同一批史料中抽出的人物写法，可能包含同一人的不同称谓（本名、单名、字、官称）。

请判断哪些指同一人。只依据所附原文语境，不得引入外部知识。不确定则各自独立成组。

写法与语境：
${ctx}

只输出 JSON 数组，每组一个数组，单独成组的也要列出：
[["写法A","写法B"],["写法C"]]

待归组：${JSON.stringify(names)}`;

/* ── 代码层：确定性推断 ───────────────────────── */
function chunkText(t, n) {
  const out = []; let b = "";
  for (const s of t.split(/(?<=[。！？；\n])/)) {
    if ((b + s).length > n && b) { out.push(b); b = s; } else b += s;
  }
  if (b.trim()) out.push(b);
  return out;
}

function verifySpans(items, src) {
  const ok = [], bad = [];
  for (const it of items) {
    const e = (it.evidence || "").trim();
    (e.length >= 4 && src.includes(e) ? ok : bad).push({ ...it, evidence: e });
  }
  return { ok, bad };
}

function analyse(rows, groups, allStances) {
  const key = new Map();
  groups.forEach(g => g.forEach(n => key.set(n, g[0])));

  const byId = new Map();
  for (const r of rows) {
    const id = key.get(r.person) || r.person;
    if (!byId.has(id)) byId.set(id, { id, aliases: new Set(), hits: [] });
    byId.get(id).aliases.add(r.person);
    byId.get(id).hits.push(r);
  }

  const out = [];
  for (const p of byId.values()) {
    const stances = [...new Set(p.hits.map(h => h.stance))];
    const acts = [...new Set(p.hits.flatMap(h => h.acts || []))];
    const flags = [];

    const term = acts.filter(a => TERMINAL.includes(a));
    if (term.length > 1)
      flags.push({ w: 5, k: `终局互斥：${term.join(" ↔ ")}`, why: "同一人被记为两种不相容的结局，必有一方曲笔。" });
    for (const [a, b] of PAIRS)
      if (acts.includes(a) && acts.includes(b))
        flags.push({ w: 4, k: `记载互斥：${a} ↔ ${b}`, why: "两处断言不能同真。" });

    /* 纪年歧异 */
    const byAct = new Map();
    for (const h of p.hits) for (const a of (h.acts || [])) {
      if (!h.time) continue;
      if (!byAct.has(a)) byAct.set(a, new Set());
      byAct.get(a).add(h.time);
    }
    for (const [a, ts] of byAct)
      if (ts.size > 1)
        flags.push({ w: 4, k: `纪年歧异（${a}）：${[...ts].join(" / ")}`, why: "同一事系于不同时间，考异之常见入口。" });

    /* 单方独载 —— 只标记，不断言 */
    const missing = allStances.filter(s => !stances.includes(s));
    if (allStances.length > 1 && missing.length)
      flags.push({ w: 3, k: `仅见于「${stances.join("、")}」`, why: `「${missing.join("、")}」无载。可能是讳饰，也可能该书本不记此类事，或材料散佚。需比对同类人物的正常著录率再判断。` });

    if (p.hits.length > 1 && !flags.length)
      flags.push({ w: 1, k: "多处互见，无冲突", why: "记载彼此一致，可作为交叉佐证。" });

    out.push({ ...p, aliases: [...p.aliases], stances, acts, flags, score: flags.reduce((s, f) => s + f.w, 0) });
  }
  return out.sort((a, b) => b.score - a.score);
}

/* ── 界面 ─────────────────────────────────────── */
export default function App() {
  const [srcs, setSrcs] = useState([
    { id: 1, name: "史源甲", stance: "南朝", text: "" },
    { id: 2, name: "史源乙", stance: "北朝", text: "" },
  ]);
  const [size, setSize] = useState(700);
  const [busy, setBusy] = useState(false);
  const [prog, setProg] = useState("");
  const [res, setRes] = useState(null);
  const [drop, setDrop] = useState(0);
  const [marks, setMarks] = useState({});
  const [err, setErr] = useState("");

  const upd = (id, k, v) => setSrcs(s => s.map(x => x.id === id ? { ...x, [k]: v } : x));

  async function run() {
    setBusy(true); setErr(""); setRes(null); setMarks({}); setDrop(0);
    try {
      const live = srcs.filter(s => s.text.trim());
      if (!live.length) throw new Error("先贴史料。");

      const rows = []; let bad = 0;
      for (const s of live) {
        const cks = chunkText(s.text, size);
        for (let i = 0; i < cks.length; i++) {
          setProg(`抽取 ${s.name} · ${i + 1}/${cks.length}`);
          let items = [];
          try { items = await callModel(extractPrompt(cks[i])); } catch { items = []; }
          const { ok, bad: b } = verifySpans(items, cks[i]);
          bad += b.length;
          ok.forEach(o => o.person && rows.push({ ...o, stance: s.stance, src: s.name, chunk: i + 1 }));
        }
      }
      setDrop(bad);

      const names = [...new Set(rows.map(r => r.person))];
      let groups = names.map(n => [n]);
      if (names.length > 1) {
        setProg("人物归一…");
        const ctx = rows.slice(0, 40).map(r => `${r.person}｜${r.title || "—"}｜${r.evidence.slice(0, 30)}`).join("\n");
        try {
          const g = await callModel(aliasPrompt(names, ctx));
          if (Array.isArray(g) && g.length) groups = g.filter(x => Array.isArray(x) && x.length);
        } catch { /* 归一失败则各自独立 */ }
      }

      setRes(analyse(rows, groups, [...new Set(live.map(s => s.stance))]));
    } catch (e) { setErr(String(e.message || e)); }
    setBusy(false); setProg("");
  }

  const exportJSON = () => {
    const blob = new Blob([JSON.stringify(res.map(p => ({ ...p, 人工判定: marks[p.id] || "未判" })), null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "shiliao_findings.json"; a.click();
  };

  const MARK = [
    { v: "核实", i: Check, c: C.qing }, { v: "存疑", i: HelpCircle, c: C.gold }, { v: "否定", i: Ban, c: C.zhu },
  ];

  return (
    <div style={{ background: C.ink, color: C.text, minHeight: "100%" }} className="p-5">
      <div className="max-w-5xl mx-auto">

        <div className="flex items-baseline gap-3 mb-1">
          <h1 style={{ fontFamily: SERIF, color: C.paper }} className="text-2xl">互見</h1>
          <span style={{ color: C.zhu }} className="text-xs tracking-widest">史料交叉比對</span>
        </div>
        <p style={{ color: C.mute }} className="text-xs mb-5 leading-relaxed">
          模型只做抽取與人物歸一；矛盾、歧異、獨載全部由程式判定，邏輯可審。
          凡引文未在原文逐字出現者一律丟棄。每條結論須回查原文。
        </p>

        <div className="grid md:grid-cols-2 gap-3 mb-4">
          {srcs.map(s => (
            <div key={s.id} style={{ background: C.panel, border: `1px solid ${C.line}` }} className="rounded p-3">
              <div className="flex items-center gap-2 mb-2">
                <input value={s.name} onChange={e => upd(s.id, "name", e.target.value)}
                  style={{ background: "transparent", color: C.paper, borderBottom: `1px solid ${C.line}` }}
                  className="text-sm flex-1 outline-none py-1" />
                <input value={s.stance} onChange={e => upd(s.id, "stance", e.target.value)}
                  style={{ background: C.ink, color: C.qing, border: `1px solid ${C.line}` }}
                  className="text-xs rounded px-2 py-1 w-20 outline-none" placeholder="立場" />
                {srcs.length > 1 && <button onClick={() => setSrcs(x => x.filter(y => y.id !== s.id))}
                  style={{ color: C.mute }}><X size={14} /></button>}
              </div>
              <textarea value={s.text} onChange={e => upd(s.id, "text", e.target.value)}
                placeholder="貼一卷史料。立場欄可填「南朝／北朝」「官修／私撰」「宋／金」等，用於偵測單方獨載。"
                style={{ background: C.paper, color: "#1A1A1A", fontFamily: SERIF, lineHeight: 1.9 }}
                className="w-full h-40 rounded p-3 text-sm outline-none resize-y" />
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-3 mb-5">
          <button onClick={run} disabled={busy} style={{ background: busy ? C.line : C.zhu, color: "#fff" }}
            className="rounded px-4 py-2 text-sm flex items-center gap-2 disabled:opacity-60">
            <Play size={14} />{busy ? "處理中" : "開始比對"}
          </button>
          <button onClick={() => setSrcs(s => [...s, { id: Date.now(), name: "新史源", stance: "", text: "" }])}
            style={{ color: C.qing, border: `1px solid ${C.line}` }} className="rounded px-3 py-2 text-xs flex items-center gap-2">
            <Plus size={13} />加一部書
          </button>
          <label style={{ color: C.mute }} className="text-xs flex items-center gap-2">分段字數
            <input type="number" value={size} min={200} max={1500} onChange={e => setSize(+e.target.value)}
              style={{ background: C.panel, color: C.text, border: `1px solid ${C.line}` }}
              className="w-20 rounded px-2 py-1 outline-none" /></label>
          {res && <button onClick={exportJSON} style={{ color: C.mute, border: `1px solid ${C.line}` }}
            className="rounded px-3 py-2 text-xs flex items-center gap-2 ml-auto"><Download size={13} />匯出標註</button>}
          {prog && <span style={{ color: C.qing }} className="text-xs">{prog}</span>}
        </div>

        {err && <div style={{ background: C.panel, borderLeft: `3px solid ${C.zhu}` }} className="rounded p-3 text-sm mb-4">{err}</div>}

        {res && (
          <>
            <div className="flex flex-wrap gap-4 mb-4 text-xs">
              <span style={{ color: C.mute }} className="flex items-center gap-1"><Users size={13} />{res.length} 人</span>
              <span style={{ color: drop ? C.zhu : C.mute }} className="flex items-center gap-1">
                <ShieldCheck size={13} />攔下 {drop} 條偽引</span>
            </div>

            {!res.length && (
              <div style={{ background: C.panel, border: `1px dashed ${C.line}`, color: C.mute }}
                className="rounded p-8 text-center text-sm">未見著錄。此段無可支撐的人物記載。</div>
            )}

            <div className="space-y-3">
              {res.map(p => (
                <div key={p.id} style={{ background: C.panel, border: `1px solid ${C.line}` }} className="rounded">
                  <div className="flex flex-wrap items-center gap-3 px-4 py-3" style={{ borderBottom: `1px solid ${C.line}` }}>
                    <span style={{ fontFamily: SERIF, color: C.paper }} className="text-lg">{p.id}</span>
                    {p.aliases.length > 1 && <span style={{ color: C.mute }} className="text-xs">
                      異稱：{p.aliases.filter(a => a !== p.id).join("、")}</span>}
                    {p.stances.map(s => <span key={s} style={{ color: C.qing, border: `1px solid ${C.line}` }}
                      className="text-xs rounded px-1.5 py-0.5">{s}</span>)}
                    <div className="flex gap-1 ml-auto">
                      {MARK.map(m => {
                        const on = marks[p.id] === m.v;
                        return <button key={m.v} onClick={() => setMarks(x => ({ ...x, [p.id]: on ? null : m.v }))}
                          title={m.v} style={{ background: on ? m.c : "transparent", color: on ? "#fff" : C.mute, border: `1px solid ${C.line}` }}
                          className="rounded px-2 py-1"><m.i size={12} /></button>;
                      })}
                    </div>
                  </div>

                  {p.flags.length > 0 && (
                    <div className="px-4 py-3 space-y-2" style={{ borderBottom: `1px solid ${C.line}` }}>
                      {p.flags.map((f, i) => (
                        <div key={i} className="flex gap-2 items-start">
                          <AlertTriangle size={13} className="mt-0.5 shrink-0" style={{ color: f.w >= 4 ? C.zhu : C.mute }} />
                          <div><span style={{ color: f.w >= 4 ? C.zhu : C.text }} className="text-xs">{f.k}</span>
                            <span style={{ color: C.mute }} className="text-xs ml-2">{f.why}</span></div>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="px-4 py-3 space-y-2">
                    {p.hits.map((h, i) => (
                      <div key={i} className="flex gap-3">
                        <div className="shrink-0 w-28 pt-1">
                          <div style={{ color: C.qing }} className="text-xs truncate">{h.src}·{h.chunk}</div>
                          <div style={{ color: C.mute }} className="text-xs truncate">
                            {[h.title, h.time].filter(Boolean).join(" ") || "—"}</div>
                        </div>
                        <div className="flex-1">
                          <p style={{ background: C.paper, color: "#1A1A1A", fontFamily: SERIF, lineHeight: 1.9 }}
                            className="text-sm rounded px-3 py-2">{h.evidence}</p>
                          {h.acts?.length > 0 && <div className="flex flex-wrap gap-1 mt-1">
                            {h.acts.map(a => <span key={a} style={{ color: C.mute, border: `1px solid ${C.line}` }}
                              className="text-xs rounded px-1.5">{a}</span>)}</div>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
