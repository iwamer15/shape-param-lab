// 手順バー（①〜④）と、① 読み込む・③ 範囲を決める の画面
import { useRef, useState } from 'react'
import type { ShapeApi } from './api'
import type { CheckResult, DoeSettings, ModelInfo, Param, RangeResult } from './types'
import { PALETTE, fmt } from './util'

export type Step = 1 | 2 | 3 | 4
export const STEPS: { n: Step; title: string; hint: string }[] = [
  { n: 1, title: '形状を読み込む', hint: 'STEP・STL・INP' },
  { n: 2, title: '変えたい所を選ぶ', hint: '面をクリック' },
  { n: 3, title: '範囲を決める', hint: '最小〜最大' },
  { n: 4, title: '形状を作る', hint: '一括生成' },
]

export function StepBar({ step, enabled, done, onStep }: {
  step: Step; enabled: (n: Step) => boolean; done: (n: Step) => boolean; onStep: (n: Step) => void
}) {
  return (
    <nav className="sl-steps">
      {STEPS.map(s => (
        <button key={s.n} className={`sl-step ${step === s.n ? 'on' : ''} ${done(s.n) ? 'done' : ''}`}
          disabled={!enabled(s.n)} onClick={() => onStep(s.n)}>
          <span className="sl-step-n">{done(s.n) && step !== s.n ? '✓' : s.n}</span>
          <span className="sl-step-t">{s.title}<small>{s.hint}</small></span>
        </button>
      ))}
    </nav>
  )
}

// ---- ① 読み込む ---------------------------------------------------------------

const ACCEPT = '.step,.stp,.iges,.igs,.brep,.brp,.stl,.inp'

interface LoadProps {
  models: { name: string }[]
  projects: { name: string }[]
  model: string
  info: ModelInfo | null
  busy: boolean
  onUpload: (f: File) => void
  onOpen: (name: string) => void
  onProject: (name: string) => void
  onDemo: (kind: 'step' | 'stl' | 'fillet') => void
  seg: number
  onResegment: (angle: number) => void
  onNext: () => void
}

export function LoadStep(p: LoadProps) {
  const file = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [segDraft, setSegDraft] = useState(p.seg)
  const [adv, setAdv] = useState(false)
  const m = p.info?.model
  return (
    <div className="sl-stepbody">
      <h2>① 形状を読み込みます</h2>
      <div className={`sl-drop ${over ? 'over' : ''}`} onClick={() => file.current?.click()}
        onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
        onDrop={e => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files?.[0]; if (f) p.onUpload(f) }}>
        <div className="sl-drop-icon">⬆</div>
        <div><b>ここにファイルをドロップ</b>、またはクリックして選ぶ</div>
        <div className="sl-sub">CAD: STEP・IGES ／ メッシュ: STL・INP（Abaqus・CalculiX）</div>
        <input ref={file} type="file" hidden accept={ACCEPT}
          onChange={e => { const f = e.target.files?.[0]; if (f) p.onUpload(f); e.target.value = '' }} />
      </div>

      {p.info && m && (
        <div className="sl-loaded">
          <div><b>{p.model}</b> <span className="sl-sub">{m.label}</span></div>
          <div className="sl-sub">
            {m.type === 'mesh'
              ? <>面を自動で見分けました: 平らな面 {m.planes}・円筒（穴・ボス）{m.cylinders}・円錐（抜き勾配）{m.cones ?? 0}
                ・角の丸み {p.info.faces.filter(f => f.fillet_r != null).length}・その他 {(m.others ?? 0) + (m.spheres ?? 0)}</>
              : <>面 {m.faces} 枚</>}
          </div>
          {m.warnings.length > 0 && <div className="sl-err">⚠ {m.warnings.join(' / ')}</div>}
          <button className="sl-btn sl-primary sl-next" onClick={p.onNext}>次へ: 変えたい所を選ぶ →</button>
          {m.type === 'mesh' && (
            <div className="sl-adv">
              <button className="sl-link" onClick={() => setAdv(!adv)}>{adv ? '▾' : '▸'} 面の見分け方を調整する（うまく分かれないとき）</button>
              {adv && (
                <>
                  <div className="sl-row">
                    <label title="曲面の三角形の向きが、当てはめた面の向きからどれだけずれてよいか">向きの許容</label>
                    <input type="range" min={5} max={89} step={1} value={segDraft} onChange={e => setSegDraft(parseInt(e.target.value))} />
                    <span className="sl-num-s">{segDraft}°</span>
                    <button className="sl-btn sl-btn-s" disabled={segDraft === p.seg || p.busy} onClick={() => p.onResegment(segDraft)}>やり直す</button>
                  </div>
                  <p className="sl-hint">粗いメッシュで円筒が細かく分かれてしまうときは大きくしてください。
                    頂点が面から {fmt(m.seg_tol, 3)} mm 以内なら同じ面とみなしています。</p>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {p.models.length > 0 && (
        <>
          <h3>読み込んだことのある形状</h3>
          <div className="sl-list">
            {p.models.map(x => (
              <button key={x.name} className={`sl-list-item ${x.name === p.model ? 'on' : ''}`} disabled={p.busy} onClick={() => p.onOpen(x.name)}>
                <span>{/\.(stl|inp)$/i.test(x.name) ? '▦' : '◇'}</span>{x.name}
              </button>
            ))}
          </div>
        </>
      )}
      {p.projects.length > 0 && (
        <>
          <h3>保存した設定から続ける</h3>
          <select value="" onChange={e => e.target.value && p.onProject(e.target.value)}>
            <option value="">（選ぶ）</option>
            {p.projects.map(x => <option key={x.name} value={x.name}>{x.name}</option>)}
          </select>
        </>
      )}
      <h3>試しに触ってみる</h3>
      <div className="sl-btns">
        <button className="sl-btn" disabled={p.busy} onClick={() => p.onDemo('step')}>デモ（CAD）</button>
        <button className="sl-btn" disabled={p.busy} onClick={() => p.onDemo('stl')}>デモ（STL）</button>
        <button className="sl-btn" disabled={p.busy} onClick={() => p.onDemo('fillet')}>デモ（角の丸み付き STL）</button>
      </div>
    </div>
  )
}

// ---- ③ 範囲を決める -------------------------------------------------------------

interface RangeProps {
  api: ShapeApi
  model: string
  seg: number
  morphMethod: string
  params: Param[]
  checks: Record<string, CheckResult>
  doe: DoeSettings
  onDoe: (d: DoeSettings) => void
  onChange: (p: Param) => void
  onNext: () => void
}

const r05 = (v: number) => Math.round(v * 2) / 2

export function RangeStep(p: RangeProps) {
  const [res, setRes] = useState<RangeResult[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [adv, setAdv] = useState((p.doe.constraints ?? []).some(c => c.trim()))
  const active = p.params.filter(x => x.enabled !== false)
  const bad = active.filter(x => x.min >= x.max)
  const check = async () => {
    setBusy(true); setErr(''); setRes(null)
    try { setRes(await p.api.rangeCheck(p.model, p.params, p.morphMethod, p.seg, p.doe.constraints ?? [])) }
    catch (e) { setErr(String((e as Error).message)) }
    setBusy(false)
  }
  const ngCount = res?.filter(r => !r.ok).length ?? 0
  return (
    <div className="sl-stepbody">
      <h2>③ それぞれの寸法を、どこからどこまで変えるか決めます</h2>
      <p className="sl-hint">この範囲の中で、いろいろな組み合わせの形を作ります。</p>
      {p.params.map((x, i) => {
        const base = p.checks[x.id]?.base
        if (base == null) return null
        const span = (x.max - x.min) || 1
        const pos = Math.min(100, Math.max(0, ((base - x.min) / span) * 100))
        const set = (w: number) => p.onChange({ ...x, min: r05(base - w), max: r05(base + w) })
        return (
          <div key={x.id} className={`sl-range-card ${x.enabled === false ? 'sl-card-off' : ''}`}>
            <div className="sl-card-head">
              <span className="sl-swatch" style={{ background: PALETTE[i % PALETTE.length] }} />
              <b>{x.name || x.key}</b>
              <span className="sl-sub">元 {x.kind === 'diameter' ? 'φ' : ''}{fmt(base)} mm</span>
              <label className="sl-mini" style={{ marginLeft: 'auto' }} title="外すと、この寸法は元の値のまま">
                <input type="checkbox" checked={x.enabled !== false} onChange={e => p.onChange({ ...x, enabled: e.target.checked })} />振る
              </label>
            </div>
            <div className="sl-rangebar">
              <div className="sl-rangebar-track" />
              <div className="sl-rangebar-base" style={{ left: `${pos}%` }} title="元の値" />
            </div>
            <div className="sl-range">
              <input type="number" value={x.min} step={x.step || 0.5} onChange={e => p.onChange({ ...x, min: parseFloat(e.target.value) })} />
              <span>〜</span>
              <input type="number" value={x.max} step={x.step || 0.5} onChange={e => p.onChange({ ...x, max: parseFloat(e.target.value) })} />
              <span className="sl-unit">mm</span>
              <span className="sl-quick">
                {[0.1, 0.2, 0.3].map(r => (
                  <button key={r} className="sl-btn sl-btn-s" onClick={() => set(Math.max(Math.abs(base) * r, 0.5))}>±{r * 100}%</button>
                ))}
              </span>
            </div>
            {(base < x.min || base > x.max) && <div className="sl-err-s">元の値が範囲の外です</div>}
            {x.min >= x.max && <div className="sl-err-s">最小が最大以上になっています</div>}
          </div>
        )
      })}

      <div className="sl-adv">
        <button className="sl-link" onClick={() => setAdv(!adv)}>{adv ? '▾' : '▸'} 寸法どうしの条件（任意）</button>
        {adv && (
          <>
            <p className="sl-hint">例: リブの上面がボスより高くならないように、など。1 行に 1 つ、下の記号で書きます。</p>
            <textarea className="sl-cons" rows={Math.max(2, (p.doe.constraints?.length ?? 0) + 1)} spellCheck={false}
              placeholder="例: rib_h <= bossB_h - 3"
              value={(p.doe.constraints ?? []).join('\n')}
              onChange={e => p.onDoe({ ...p.doe, constraints: e.target.value.split('\n') })} />
            <table className="sl-keys"><tbody>
              {active.map(x => <tr key={x.id}><td><code>{x.key}</code></td><td>{x.name}</td></tr>)}
            </tbody></table>
          </>
        )}
      </div>

      <div className="sl-btns">
        <button className="sl-btn" disabled={busy || !active.length || bad.length > 0} onClick={check}
          title="各寸法を最小・最大にしても形が作れるかを、先に確かめます">{busy ? '確かめています…' : '端の値で作れるか確かめる'}</button>
      </div>
      {err && <div className="sl-err">⚠ {err}</div>}
      {res && (
        <>
          <div className={ngCount ? 'sl-err' : 'sl-okbox'}>
            {ngCount ? `${ngCount} 件、形が作れませんでした。範囲を狭めてください。` : 'すべて作れました。'}
          </div>
          <ul className="sl-range-list">
            {res.map((r, i) => (
              <li key={i} className={r.skipped ? 'skip' : r.ok ? 'ok' : 'ng'}>
                {r.skipped ? '－' : r.ok ? '✓' : '✗'} {r.label}
                {r.skipped && <span className="sl-sub">（条件を満たさないので確かめない）</span>}
                {!r.ok && !r.skipped && <div className="sl-err-s">{r.error}</div>}
              </li>
            ))}
          </ul>
        </>
      )}
      <button className="sl-btn sl-primary sl-next" disabled={!active.length || bad.length > 0} onClick={p.onNext}>次へ: 形状を作る →</button>
    </div>
  )
}
