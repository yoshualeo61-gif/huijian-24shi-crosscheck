import React, { useMemo, useState } from "react";
import { Check, HelpCircle, Ban, Download, Upload, AlertTriangle, Eye, PenLine } from "lucide-react";

/*
  互見 · 標注界面

  這個界面**不做判定**。

  早先的版本在 JS 裡重寫了一遍確定性層（chunk、逐字校驗、矛盾／獨載判定），
  還用模型做人物歸一。結果是同一份材料、兩套程式、兩種結論，而使用者無從
  察覺哪一套是對的；模型歸一更是把判斷交回給模型，正是本專案的設計要排除
  的那一類錯誤（「模型直接下判斷 → 不能發現」）。

  所以現在：判定一律由 Python 產出，這裡只做兩件事 ——

    看  讀 work/findings.json，照 Python 算好的權重與旗標顯示，不重算。
    標  讀 biaozhu.py sample 產出的標注檔，記錄人的判斷，匯出回去給
        biaozhu.py score 計分。

  按鈕上的可選值與每軌的指南都從標注檔的 meta 讀取，不在此處另抄一份 ——
  抄一份就會各自漂移，而漂移是查不出來的。

  也因此這個界面不呼叫任何 API，不需要 key，離線可用。
*/

const C = {
  ink: "#13151B", panel: "#1B1E27", line: "#2B303E", paper: "#EAE5D9",
  zhu: "#D8483A", qing: "#4A80AE", gold: "#B08A44", mute: "#848B99", text: "#D5D8DF",
};
const SERIF = "'Songti SC','Noto Serif CJK SC','Source Han Serif SC','SimSun',serif";

const TRACK_NAME = { A: "抽取精度", B: "信號效度", C: "負對照（攔截）" };

/* 判斷值的顏色：肯定／保留／否定三類，其餘中性 */
const VAL_COLOR = (v) => {
  if (["ok", "correct", "yes"].includes(v)) return C.qing;
  if (["undecidable", "na"].includes(v)) return C.gold;
  if (["wrong", "unsupported", "false_reject", "no"].includes(v)) return C.zhu;
  return C.mute;
};

function Panel({ children, style }) {
  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, ...style }}
      className="rounded p-4">{children}</div>
  );
}

function Quote({ children }) {
  return (
    <div style={{ fontFamily: SERIF, color: C.paper, borderLeft: `2px solid ${C.zhu}` }}
      className="pl-3 py-1 text-base leading-relaxed">「{children}」</div>
  );
}

/* ── 讀檔 ─────────────────────────────────────── */
function readFile(file, onDone, onErr) {
  const r = new FileReader();
  r.onload = () => {
    try { onDone(JSON.parse(String(r.result))); }
    catch (e) { onErr(`JSON 解析失敗：${e.message}`); }
  };
  r.onerror = () => onErr("讀檔失敗");
  r.readAsText(file, "utf-8");
}

/* 標注檔：{meta, items}；findings：陣列且元素有 flags/hits */
const shapeOf = (d) => {
  if (d && !Array.isArray(d) && Array.isArray(d.items)) return "annot";
  if (Array.isArray(d) && d.length && d[0] && Array.isArray(d[0].hits)) return "findings";
  if (Array.isArray(d) && d.length && d[0] && d[0].track) return "annot-bare";
  return null;
};

/* ── 檢視：顯示 Python 的判定，不重算 ───────────── */
function FindingsView({ data }) {
  return (
    <div className="space-y-3">
      <Panel style={{ borderColor: C.qing }}>
        <div className="flex gap-2 items-start text-xs" style={{ color: C.mute }}>
          <Eye size={14} style={{ color: C.qing, flexShrink: 0, marginTop: 2 }} />
          <span>
            以下權重與旗標全部由 <code>huijian.py verify</code> 算出，此處照原樣顯示。
            本界面不參與判定，所以不會與命令列給出不同的結論。
          </span>
        </div>
      </Panel>

      {data.map((p, i) => (
        <Panel key={i}>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span style={{ fontFamily: SERIF, color: C.paper }} className="text-lg">
              {p.person}</span>
            <span className="text-xs" style={{ color: C.qing }}>
              {(p.stances || []).join(" | ")}</span>
            <span className="text-xs" style={{ color: C.gold }}>權重 {p.score}</span>
            {p.person_raw && p.person_raw !== p.person && (
              <span className="text-xs" style={{ color: C.mute }}>
                抽取原作「{p.person_raw}」</span>)}
          </div>

          {(p.known || []).map((k, j) => (
            <div key={j} className="text-xs mt-2" style={{ color: C.gold }}>
              ◆ 考異已及{k.topic ? `（${k.topic}）` : ""}：{k.source || "未註明出處"}
            </div>
          ))}

          <div className="mt-3 space-y-2">
            {(p.flags || []).map((f, j) => {
              const [w, k, why] = Array.isArray(f) ? f : [f.w, f.k, f.why];
              return (
                <div key={j} className="text-sm">
                  <span style={{ color: w >= 4 ? C.zhu : C.mute }}>
                    {w >= 4 ? "⚠" : "·"} {k}</span>
                  <div className="text-xs mt-1 whitespace-pre-line"
                    style={{ color: C.mute }}>{why}</div>
                </div>
              );
            })}
          </div>

          <div className="mt-3 space-y-2">
            {(p.hits || []).map((h, j) => (
              <div key={j}>
                <div className="text-xs" style={{ color: C.mute }}>
                  〔{h.stance}〕《{h.book}·{h.juan}》 {h.time || "—"}
                  {h.ev_start != null && <span> · 位址 {h.ev_start}–{h.ev_end}</span>}
                  {h.acts && h.acts.length ? ` · ${h.acts.join("、")}` : ""}
                </div>
                <Quote>{h.evidence}</Quote>
              </div>
            ))}
          </div>
        </Panel>
      ))}
    </div>
  );
}

/* ── 標注 ─────────────────────────────────────── */
function AnnotView({ doc, setDoc }) {
  const meta = doc.meta || {};
  const values = meta.values || {};
  const guide = meta.guide || {};
  const items = doc.items || [];
  const [track, setTrack] = useState("A");
  const [onlyTodo, setOnlyTodo] = useState(false);

  const judged = (it) =>
    Object.values(it.verdict || {}).some((v) => v !== null && v !== undefined);

  const shown = items.filter((it) => it.track === track && (!onlyTodo || !judged(it)));
  const stat = useMemo(() => {
    const s = {};
    for (const it of items) {
      s[it.track] = s[it.track] || { n: 0, done: 0 };
      s[it.track].n++;
      if (judged(it)) s[it.track].done++;
    }
    return s;
  }, [items]);

  const set = (id, field, v) =>
    setDoc({
      ...doc,
      items: items.map((it) =>
        it.id === id
          ? { ...it, verdict: { ...it.verdict, [field]: it.verdict?.[field] === v ? null : v } }
          : it),
    });

  const note = (id, v) =>
    setDoc({ ...doc, items: items.map((it) => (it.id === id ? { ...it, note: v } : it)) });

  const exportJSON = () => {
    const blob = new Blob([JSON.stringify(doc, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "annot_judged.json";
    a.click();
  };

  return (
    <div className="space-y-3">
      <Panel>
        <div className="flex flex-wrap gap-2 items-center justify-between">
          <div className="flex gap-2">
            {["A", "B", "C"].map((t) => {
              const st = stat[t];
              return (
                <button key={t} onClick={() => setTrack(t)} disabled={!st}
                  style={{
                    background: track === t ? C.zhu : "transparent",
                    border: `1px solid ${track === t ? C.zhu : C.line}`,
                    color: st ? (track === t ? C.paper : C.text) : C.line,
                  }} className="px-3 py-1 rounded text-xs">
                  {t} · {TRACK_NAME[t]}{st ? ` ${st.done}/${st.n}` : " 0"}
                </button>
              );
            })}
          </div>
          <div className="flex gap-3 items-center">
            <label className="text-xs flex gap-1 items-center" style={{ color: C.mute }}>
              <input type="checkbox" checked={onlyTodo}
                onChange={(e) => setOnlyTodo(e.target.checked)} />
              只看未標
            </label>
            <button onClick={exportJSON}
              style={{ background: C.qing, color: C.ink }}
              className="px-3 py-1 rounded text-xs flex gap-1 items-center">
              <Download size={13} /> 匯出
            </button>
          </div>
        </div>
        <div className="text-xs mt-3" style={{ color: C.mute }}>
          匯出後交給 <code>python biaozhu.py score work/ --name annot_judged.json</code> 計分。
          兩人各標一份，再加 <code>--second</code> 就能算 κ。
        </div>
      </Panel>

      {(guide[track] || []).length > 0 && (
        <Panel style={{ borderColor: C.gold }}>
          <div className="text-xs mb-1" style={{ color: C.gold }}>
            {track} 軌判斷指南（取自標注檔，與命令列的本子同源）
          </div>
          {guide[track].map((g, i) => (
            <div key={i} className="text-xs leading-relaxed" style={{ color: C.mute }}>{g}</div>
          ))}
        </Panel>
      )}

      {shown.length === 0 && (
        <Panel><div className="text-xs" style={{ color: C.mute }}>
          這一軌沒有待顯示的條目。</div></Panel>
      )}

      {shown.map((it) => (
        <Panel key={it.id}>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span style={{ color: C.gold }} className="text-xs">{it.id}</span>
            {it.track === "A" && (
              <span className="text-xs" style={{ color: C.mute }}>
                《{it.book}·{it.juan}》[{it.stance}]
                {it.addr && it.addr[0] != null ? ` 位址 ${it.addr[0]}–${it.addr[1]}` : ""}
              </span>
            )}
            {it.track === "B" && (
              <span className="text-xs" style={{ color: C.mute }}>
                {it.person} · 權重 {it.weight}</span>
            )}
            {it.track === "C" && (
              <span className="text-xs" style={{ color: C.zhu }}>
                攔截理由：{it.why_rejected}</span>
            )}
          </div>

          {it.track === "A" && (
            <div className="mt-2">
              <div className="text-xs" style={{ color: C.mute }}>
                person=<b style={{ color: C.text }}>{String(it.person)}</b>
                {"  "}title={String(it.title || "")}
                {"  "}place={String(it.place || "")}
                {"  "}time={String(it.time || "")}
              </div>
              <div className="text-xs" style={{ color: C.mute }}>
                acts={(it.acts || []).join("、") || "—"}
              </div>
              {it.person_normalized && it.person_normalized !== it.person && (
                <div className="text-xs mt-1" style={{ color: C.gold }}>
                  歸一後作「{it.person_normalized}」—— 人工確認過的合併，不在本軌評判範圍
                </div>
              )}
              <Quote>{it.evidence}</Quote>
            </div>
          )}

          {it.track === "B" && (
            <div className="mt-2">
              <div className="text-sm" style={{ color: C.text }}>旗標：{it.flag}</div>
              <div className="text-xs mt-1 whitespace-pre-line" style={{ color: C.mute }}>
                {it.why}</div>
              <div className="mt-2 space-y-2">
                {(it.hits || []).map((h, j) => (
                  <div key={j}>
                    <div className="text-xs" style={{ color: C.mute }}>
                      〔{h.stance}〕《{h.book}·{h.juan}》 {h.time || "—"}
                      {h.acts && h.acts.length ? ` · ${h.acts.join("、")}` : ""}
                    </div>
                    <Quote>{h.evidence}</Quote>
                  </div>
                ))}
              </div>
            </div>
          )}

          {it.track === "C" && (
            <div className="mt-2">
              <div className="text-xs" style={{ color: C.mute }}>
                《{it.book}·{it.juan}》 person={String(it.person || "")}
                {"  "}是否為 chunk 子串：{String(it.evidence_in_chunk_substring)}
              </div>
              <div className="text-xs mt-1" style={{ color: C.mute }}>聲稱引文：</div>
              <Quote>{it.evidence}</Quote>
              <div className="text-xs mt-2" style={{ color: C.mute }}>chunk：</div>
              <div style={{ fontFamily: SERIF, color: C.text, background: C.ink }}
                className="text-sm p-2 rounded max-h-40 overflow-auto whitespace-pre-wrap">
                {it.chunk}
              </div>
            </div>
          )}

          <div className="mt-3 space-y-2">
            {Object.entries(values[it.track] || {}).map(([field, opts]) => (
              <div key={field} className="flex flex-wrap gap-2 items-center">
                <span className="text-xs w-28" style={{ color: C.mute }}>{field}</span>
                {opts.map((v) => {
                  const on = it.verdict?.[field] === v;
                  return (
                    <button key={v} onClick={() => set(it.id, field, v)}
                      style={{
                        background: on ? VAL_COLOR(v) : "transparent",
                        border: `1px solid ${on ? VAL_COLOR(v) : C.line}`,
                        color: on ? C.ink : C.text,
                      }} className="px-2 py-0.5 rounded text-xs">{v}</button>
                  );
                })}
              </div>
            ))}
            <input value={it.note || ""} onChange={(e) => note(it.id, e.target.value)}
              placeholder="備註（可空）"
              style={{ background: C.ink, border: `1px solid ${C.line}`, color: C.text }}
              className="w-full px-2 py-1 rounded text-xs" />
          </div>
        </Panel>
      ))}
    </div>
  );
}

/* ── 殼 ───────────────────────────────────────── */
export default function App() {
  const [doc, setDoc] = useState(null);
  const [findings, setFindings] = useState(null);
  const [err, setErr] = useState("");

  const load = (f) => {
    setErr("");
    readFile(f, (d) => {
      const k = shapeOf(d);
      if (k === "annot") setDoc(d);
      else if (k === "annot-bare") setDoc({ meta: {}, items: d });
      else if (k === "findings") setFindings(d);
      else setErr("認不出這個檔。請給 biaozhu.py sample 產出的標注檔，或 findings.json。");
    }, setErr);
  };

  return (
    <div style={{ background: C.ink, color: C.text, minHeight: "100%" }} className="p-5">
      <div className="max-w-5xl mx-auto">

        <div className="flex items-baseline gap-3 mb-1 flex-wrap">
          <h1 style={{ fontFamily: SERIF, color: C.paper }} className="text-2xl">互見</h1>
          <span style={{ color: C.zhu }} className="text-xs tracking-widest">標注界面</span>
        </div>
        <div className="text-xs mb-4" style={{ color: C.mute }}>
          判定由 <code>huijian.py</code> 產出；此處只負責顯示與記錄人的判斷，不重算、不呼叫模型。
        </div>

        <Panel style={{ borderColor: C.zhu, marginBottom: 12 }}>
          <div className="flex gap-2 items-start text-xs" style={{ color: C.mute }}>
            <AlertTriangle size={14} style={{ color: C.zhu, flexShrink: 0, marginTop: 2 }} />
            <div>
              <div style={{ color: C.text }}>本界面不做判定。</div>
              早先的版本在 JS 裡重寫了一遍確定性層，又用模型做人物歸一，於是同一份
              材料會從命令列和界面得到兩種結論，而使用者無從察覺哪個對。現在判定只有
              一處（Python），模型也不再參與歸一 —— 歸一改由
              <code> aliases.json </code>人工確認。
            </div>
          </div>
        </Panel>

        <Panel style={{ marginBottom: 12 }}>
          <div className="flex flex-wrap gap-3 items-center">
            <label style={{ background: C.gold, color: C.ink }}
              className="px-3 py-1 rounded text-xs flex gap-1 items-center cursor-pointer">
              <Upload size={13} /> 載入檔案
              <input type="file" accept=".json" className="hidden"
                onChange={(e) => e.target.files?.[0] && load(e.target.files[0])} />
            </label>
            <span className="text-xs" style={{ color: C.mute }}>
              <PenLine size={11} className="inline" /> 標注檔（<code>biaozhu.py sample</code> 產出）
              或 <Eye size={11} className="inline" /> <code>findings.json</code>
            </span>
            {(doc || findings) && (
              <button onClick={() => { setDoc(null); setFindings(null); }}
                style={{ border: `1px solid ${C.line}`, color: C.mute }}
                className="px-2 py-0.5 rounded text-xs">清空</button>
            )}
          </div>
          {err && <div className="text-xs mt-2" style={{ color: C.zhu }}>{err}</div>}
        </Panel>

        {doc && <AnnotView doc={doc} setDoc={setDoc} />}
        {findings && !doc && <FindingsView data={findings} />}

        {!doc && !findings && (
          <Panel>
            <div className="text-xs leading-relaxed" style={{ color: C.mute }}>
              <div style={{ color: C.text }} className="mb-2">怎麼用</div>
              <div>1. <code>python huijian.py verify work/</code> —— 產出 findings.json</div>
              <div>2. <code>python biaozhu.py sample work/ -n 150</code> —— 產出標注檔</div>
              <div>3. 在此載入標注檔，逐條判斷，匯出</div>
              <div>4. <code>python biaozhu.py score work/ --name annot_judged.json</code></div>
              <div className="mt-2">兩人各標一份，<code>--second</code> 可算 Cohen's κ。流程見 docs/05。</div>
            </div>
          </Panel>
        )}

        <div className="text-xs mt-6 flex gap-4 flex-wrap" style={{ color: C.line }}>
          <span><Check size={11} className="inline" style={{ color: C.qing }} /> 肯定</span>
          <span><HelpCircle size={11} className="inline" style={{ color: C.gold }} /> 保留／不適用</span>
          <span><Ban size={11} className="inline" style={{ color: C.zhu }} /> 否定</span>
        </div>
      </div>
    </div>
  );
}
