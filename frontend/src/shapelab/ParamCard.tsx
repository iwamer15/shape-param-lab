// 寸法 1 件分のカード。ふだんは「名前・スライダー・値」だけ。種類・面・係数・列名は「詳細設定」に入れる
import { useState } from 'react'
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
  const [open, setOpen] = useState(false)
  const base = check?.base ?? null
  const cur = value ?? base ?? 0
  const step = p.step && p.step > 0 ? p.step : 0.1
  const set = (patch: Partial<Param>) => onChange({ ...p, ...patch })
  const refCands = p.kind === 'distance' && p.faces[0] ? parallelPlanes(faces, p.faces[0].face) : []
  const err = check?.error
  const delta = base != null ? cur - base : 0
  const unit = p.kind === 'diameter' ? 'φ' : ''

  return (
    <div className={`sl-card ${active ? 'sl-card-active' : ''} ${p.enabled === false ? 'sl-card-off' : ''}`} onClick={onSelect}>
      <div className="sl-card-head">
        <span className="sl-swatch" style={{ background: color }} />
        <input className="sl-name" value={p.name} placeholder="名前（例: 板厚）" onChange={e => set({ name: e.target.value })} />
        <button className="sl-icon" title="この寸法を消す" onClick={e => { e.stopPropagation(); onDelete() }}>✕</button>
      </div>

      <div className="sl-slider">
        <input type="range" min={p.min} max={p.max} step={step} value={Math.min(Math.max(cur, p.min), p.max)}
          disabled={!!err} onChange={e => onValue(parseFloat(e.target.value))} />
        <span className="sl-unit">{unit}</span>
        <input className="sl-num" type="number" step={step} value={fmt(cur)} disabled={!!err}
          onChange={e => onValue(e.target.value === '' ? undefined : parseFloat(e.target.value))} />
        <span className="sl-unit">mm</span>
      </div>
      <div className="sl-sub sl-card-foot">
        <span>
          元 {base == null ? '—' : `${unit}${fmt(base)}`} mm
          {base != null && Math.abs(delta) > 1e-9 && <b className={delta > 0 ? 'sl-up' : 'sl-down'}> （{delta > 0 ? '+' : ''}{fmt(delta)}）</b>}
        </span>
        <span>
          <button className="sl-link" disabled={value === undefined} onClick={e => { e.stopPropagation(); onValue(undefined) }}>元に戻す</button>
          <button className="sl-link" onClick={e => { e.stopPropagation(); setOpen(!open) }}>{open ? '▾' : '▸'} 詳細設定</button>
        </span>
      </div>
      {err && <div className="sl-err">⚠ {err}</div>}

      {open && (
        <div className="sl-detail" onClick={e => e.stopPropagation()}>
          <div className="sl-row">
            <label>種類</label>
            <select value={p.kind} onChange={e => set({ kind: e.target.value as Kind, ref_face: e.target.value === 'distance' ? p.ref_face : null })}>
              {Object.entries(kinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="sl-row sl-row-top">
            <label>動かす面</label>
            <div className="sl-chips">
              {p.faces.map((f, i) => (
                <span className="sl-chip" key={f.face} style={{ borderColor: color }}>
                  {faces[f.face]?.label ?? `#${f.face}`}
                  <span title="この面を何倍動かすか（両側に均等に広げるときは 0.5 ずつ）">×
                    <input className="sl-coef" type="number" step={0.1} value={f.coef}
                      onChange={e => set({ faces: p.faces.map((g, j) => j === i ? { ...g, coef: parseFloat(e.target.value) || 0 } : g) })} />
                  </span>
                  <button className="sl-icon" onClick={() => set({ faces: p.faces.filter((_, j) => j !== i) })}>✕</button>
                </span>
              ))}
              <button className={`sl-btn sl-btn-s ${pickMode === 'faces' ? 'sl-on' : ''}`} onClick={() => onPick(pickMode === 'faces' ? null : 'faces')}>
                {pickMode === 'faces' ? '選択中…（3D をクリック）' : '＋ 面を足す／外す'}
              </button>
            </div>
          </div>
          {p.kind === 'distance' && (
            <div className="sl-row sl-row-top">
              <label>測る基準</label>
              <div className="sl-col">
                <select value={p.ref_face ?? ''} onChange={e => set({ ref_face: e.target.value === '' ? null : parseInt(e.target.value) })}>
                  <option value="">（選んでください）</option>
                  {refCands.map(c => <option key={c.face.id} value={c.face.id}>{c.face.label}　距離 {fmt(c.dist)} mm</option>)}
                  {p.ref_face != null && !refCands.some(c => c.face.id === p.ref_face) &&
                    <option value={p.ref_face}>{faces[p.ref_face]?.label ?? `#${p.ref_face}`}</option>}
                </select>
                <button className={`sl-btn sl-btn-s ${pickMode === 'ref' ? 'sl-on' : ''}`} onClick={() => onPick(pickMode === 'ref' ? null : 'ref')}>
                  {pickMode === 'ref' ? '選択中…' : '3D で基準の面を選ぶ'}
                </button>
              </div>
            </div>
          )}
          <div className="sl-row">
            <label title="出力する conditions.csv の列名（英数字と _）">列名</label>
            <input value={p.key} onChange={e => set({ key: e.target.value })} />
          </div>
          <div className="sl-row">
            <label>一括生成</label>
            <label className="sl-mini"><input type="checkbox" checked={p.enabled !== false} onChange={e => set({ enabled: e.target.checked })} />この寸法を振る</label>
          </div>
        </div>
      )}
    </div>
  )
}
