// パラメータ 1 件分の編集カード（種類・面・範囲・スライダー）
import type { CheckResult, FaceInfo, Kind, Param } from './types'
import { fmt, parallelPlanes } from './util'

interface Props {
  p: Param
  color: string
  check?: CheckResult
  value: number | undefined
  faces: FaceInfo[]
  kinds: Record<string, string>
  active: boolean
  pickMode: 'faces' | 'ref' | null
  onSelect: () => void
  onChange: (p: Param) => void
  onValue: (v: number | undefined) => void
  onPick: (mode: 'faces' | 'ref' | null) => void
  onDelete: () => void
}

export function ParamCard({ p, color, check, value, faces, kinds, active, pickMode, onSelect, onChange, onValue, onPick, onDelete }: Props) {
  const base = check?.base ?? null
  const cur = value ?? base ?? 0
  const step = p.step && p.step > 0 ? p.step : 0.1
  const set = (patch: Partial<Param>) => onChange({ ...p, ...patch })
  const refCands = p.kind === 'distance' && p.faces[0] ? parallelPlanes(faces, p.faces[0].face) : []
  const err = check?.error
  const delta = base != null ? cur - base : 0

  return (
    <div className={`sl-card ${active ? 'sl-card-active' : ''} ${p.enabled === false ? 'sl-card-off' : ''}`} onClick={onSelect}>
      <div className="sl-card-head">
        <span className="sl-swatch" style={{ background: color }} />
        <input className="sl-name" value={p.name} placeholder="表示名（例: 板厚）" onChange={e => set({ name: e.target.value })} />
        <label className="sl-mini" title="一括生成に使うか">
          <input type="checkbox" checked={p.enabled !== false} onChange={e => set({ enabled: e.target.checked })} />使う
        </label>
        <button className="sl-icon" title="削除" onClick={e => { e.stopPropagation(); onDelete() }}>✕</button>
      </div>

      {/* スライダー（いちばんよく触る所なので上に） */}
      <div className="sl-slider">
        <input type="range" min={p.min} max={p.max} step={step} value={Math.min(Math.max(cur, p.min), p.max)}
          disabled={!!err} onChange={e => onValue(parseFloat(e.target.value))} />
        <input className="sl-num" type="number" step={step} value={fmt(cur)} disabled={!!err}
          onChange={e => onValue(e.target.value === '' ? undefined : parseFloat(e.target.value))} />
        <span className="sl-unit">mm</span>
        <button className="sl-link" disabled={value === undefined} onClick={() => onValue(undefined)} title="元の寸法に戻す">元に戻す</button>
      </div>
      <div className="sl-sub">
        元の値 {base == null ? '—' : fmt(base)} mm
        {base != null && Math.abs(delta) > 1e-9 && <b className={delta > 0 ? 'sl-up' : 'sl-down'}> （{delta > 0 ? '+' : ''}{fmt(delta)}）</b>}
      </div>
      {err && <div className="sl-err">⚠ {err}</div>}

      {active && (
        <div className="sl-detail" onClick={e => e.stopPropagation()}>
          <div className="sl-row">
            <label>種類</label>
            <select value={p.kind} onChange={e => set({ kind: e.target.value as Kind, ref_face: e.target.value === 'distance' ? p.ref_face : null })}>
              {Object.entries(kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="sl-row">
            <label>列名</label>
            <input value={p.key} onChange={e => set({ key: e.target.value })} placeholder="plate_t" />
          </div>

          <div className="sl-row sl-row-top">
            <label>動かす面</label>
            <div className="sl-chips">
              {p.faces.map((f, i) => (
                <span className="sl-chip" key={f.face} style={{ borderColor: color }}>
                  {faces[f.face]?.label ?? `#${f.face}`}
                  <span title="係数（この面を何倍動かすか。両側に広げるなら 0.5 ずつ）">×
                    <input className="sl-coef" type="number" step={0.1} value={f.coef}
                      onChange={e => set({ faces: p.faces.map((g, j) => j === i ? { ...g, coef: parseFloat(e.target.value) || 0 } : g) })} />
                  </span>
                  <button className="sl-icon" onClick={() => set({ faces: p.faces.filter((_, j) => j !== i) })}>✕</button>
                </span>
              ))}
              <button className={`sl-btn sl-btn-s ${pickMode === 'faces' ? 'sl-on' : ''}`} onClick={() => onPick(pickMode === 'faces' ? null : 'faces')}>
                {pickMode === 'faces' ? '選択中…（3D をクリック）' : '＋ 3D で面を選ぶ'}
              </button>
            </div>
          </div>

          {p.kind === 'distance' && (
            <div className="sl-row sl-row-top">
              <label>基準面</label>
              <div className="sl-col">
                <select value={p.ref_face ?? ''} onChange={e => set({ ref_face: e.target.value === '' ? null : parseInt(e.target.value) })}>
                  <option value="">（選んでください）</option>
                  {refCands.map(c => <option key={c.face.id} value={c.face.id}>{c.face.label}　距離 {fmt(c.dist)} mm</option>)}
                  {p.ref_face != null && !refCands.some(c => c.face.id === p.ref_face) &&
                    <option value={p.ref_face}>{faces[p.ref_face]?.label ?? `#${p.ref_face}`}</option>}
                </select>
                <button className={`sl-btn sl-btn-s ${pickMode === 'ref' ? 'sl-on' : ''}`} onClick={() => onPick(pickMode === 'ref' ? null : 'ref')}>
                  {pickMode === 'ref' ? '選択中…' : '3D で基準面を選ぶ'}
                </button>
              </div>
            </div>
          )}

          <div className="sl-row">
            <label>範囲</label>
            <div className="sl-range">
              <input type="number" value={p.min} step={step} onChange={e => set({ min: parseFloat(e.target.value) })} />
              <span>〜</span>
              <input type="number" value={p.max} step={step} onChange={e => set({ max: parseFloat(e.target.value) })} />
              <span className="sl-unit">刻み</span>
              <input type="number" value={p.step ?? 0} step={0.1} min={0} onChange={e => set({ step: parseFloat(e.target.value) || 0 })} />
            </div>
          </div>
          <p className="sl-hint">{hint(p.kind)}</p>
        </div>
      )}
    </div>
  )
}

function hint(k: Kind) {
  if (k === 'distance') return '動かす平面と、平行な基準面の距離を寸法にします（厚み・高さ・幅）。基準面は動きません（両側に広げたいときは基準面も「動かす面」に入れ、係数を 0.5 ずつに）。'
  if (k === 'diameter') return '円筒面の直径を寸法にします。穴なら穴径、ボスなら外径。'
  return '選んだ面を法線方向（外側が +）に動かす量そのものです。0 で元の形。'
}
