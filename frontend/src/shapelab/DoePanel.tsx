// 実験計画（どの寸法の組み合わせで作るか）・範囲チェック・一括生成
import { useEffect, useRef, useState } from 'react'
import type { ShapeApi } from './api'
import type { DoeSettings, GenRow, Job, Param, Project } from './types'
import { PALETTE, fmt, stamp } from './util'

const FORMAT_HINT: Record<string, string> = {
  stl: '表面の三角形（学習・ボクセル化用）', step: 'CAD 形状（CAE にそのまま渡す用）', inp: '節点座標だけ書き換えた解析メッシュ（要素・セット・材料はそのまま）',
}

interface Props {
  api: ShapeApi
  model: string
  params: Param[]
  doe: DoeSettings
  methods: Record<string, string>
  morphMethod: string
  seg: number
  formatsAvail: string[]
  hasErrors: boolean
  onDoe: (d: DoeSettings) => void
  onPreview: (values: Record<string, number>) => void
  project: () => Project
}

export function DoePanel({ api, model, params, doe, methods, morphMethod, seg, formatsAvail, hasErrors, onDoe, onPreview, project }: Props) {
  const active = params.filter(p => p.enabled !== false)
  const [rows, setRows] = useState<Record<string, number>[]>([])
  const [sel, setSel] = useState<number | null>(null)
  const [axes, setAxes] = useState<[string, string]>(['', ''])
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [outName, setOutName] = useState('')
  const [formats, setFormats] = useState<Record<string, boolean>>({ stl: true, step: true, inp: true })
  const [overwrite, setOverwrite] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [jobRows, setJobRows] = useState<GenRow[]>([])
  const [jobPath, setJobPath] = useState('')
  const [outputs, setOutputs] = useState<{ name: string; samples: number; path: string }[]>([])
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => { setOutName(`${model.replace(/\.[^.]+$/, '')}_${stamp()}`) }, [model])
  useEffect(() => { api.outputs().then(setOutputs).catch(() => {}) }, [api])
  useEffect(() => () => window.clearTimeout(timer.current), [])
  useEffect(() => {
    const ids = active.map(p => p.id)
    setAxes(a => [ids.includes(a[0]) ? a[0] : ids[0] ?? '', ids.includes(a[1]) ? a[1] : ids[1] ?? ids[0] ?? ''])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params])

  const plan = async () => {
    setErr(''); setBusy('plan')
    try { setRows(await api.doe(model, params, doe, seg)); setSel(null) } catch (e) { setErr(String((e as Error).message)) }
    setBusy('')
  }
  const start = async () => {
    setErr(''); setJobRows([])
    try {
      const fm = formatsAvail.filter(k => formats[k] !== false)
      const r = await api.generate({ project: project(), out_name: outName, formats: fm, method: morphMethod, overwrite,
        samples: rows.length ? rows : undefined })
      setJobPath(r.path)
      let since = 0
      const poll = async () => {
        try {
          const j = await api.job(r.job, since)
          since += j.rows.length
          setJob(j)
          if (j.rows.length) setJobRows(old => [...old, ...j.rows])
          if (j.status === 'running') timer.current = window.setTimeout(poll, 600)
          else api.outputs().then(setOutputs).catch(() => {})
        } catch (e) { setErr(String((e as Error).message)) }
      }
      poll()
    } catch (e) { setErr(String((e as Error).message)) }
  }

  const running = job?.status === 'running'
  const total = doe.method === 'grid' ? Math.pow(Math.max(2, doe.levels), active.length) : null

  const [adv, setAdv] = useState(false)
  const suggestN = Math.max(10, active.length * 10)

  return (
    <div className="sl-doe sl-stepbody">
      <h2>④ いろいろな組み合わせの形状を作ります</h2>
      <div className="sl-row sl-big">
        <label>作る数</label>
        <input type="number" min={1} max={2000} value={doe.n} disabled={doe.method === 'grid' || doe.method === 'oat'}
          onChange={e => onDoe({ ...doe, n: parseInt(e.target.value) || 1 })} />
        <span className="sl-sub">形状（目安: 寸法の数 × 10 = {suggestN} 以上）</span>
      </div>
      <p className="sl-hint">範囲の中をまんべんなく試せるように、組み合わせを自動で選びます（ラテン超方格）。</p>
      <div className="sl-adv">
        <button className="sl-link" onClick={() => setAdv(!adv)}>{adv ? '▾' : '▸'} 組み合わせの選び方（詳細）</button>
        {adv && (
          <>
            <div className="sl-row">
              <label>方法</label>
              <select value={doe.method} onChange={e => onDoe({ ...doe, method: e.target.value })}>
                {Object.entries(methods).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            {(doe.method === 'lhs' || doe.method === 'random') && (
              <div className="sl-row">
                <label>乱数の種</label>
                <input type="number" value={doe.seed} onChange={e => onDoe({ ...doe, seed: parseInt(e.target.value) || 0 })} />
              </div>
            )}
            {(doe.method === 'grid' || doe.method === 'oat') && (
              <div className="sl-row">
                <label>水準数</label>
                <input type="number" min={2} max={11} value={doe.levels} onChange={e => onDoe({ ...doe, levels: parseInt(e.target.value) || 2 })} />
                {total != null && <span className="sl-sub">→ {total} 通り</span>}
              </div>
            )}
            <p className="sl-hint">
              {doe.method === 'lhs' && 'ラテン超方格: 各寸法の範囲を作る数で等分し、どの区間も 1 回ずつ使う。少ない数で全体をまんべんなく試せる（おすすめ）。'}
              {doe.method === 'random' && 'ランダム: 範囲内で一様に。偏りが出やすい。'}
              {doe.method === 'grid' && '格子: 各寸法を等間隔に振った全組み合わせ。寸法が多いと数が爆発的に増える。'}
              {doe.method === 'oat' && '1つずつ: 他を元の値に固定し、1 つだけ振る。どの寸法が効くか（感度）を見る用。'}
            </p>
          </>
        )}
      </div>
      <div className="sl-btns">
        <button className="sl-btn" disabled={!active.length || hasErrors || !!busy} onClick={plan}>{busy === 'plan' ? '作成中…' : '組み合わせを先に見る'}</button>
      </div>
      {err && <div className="sl-err">⚠ {err}</div>}

      {rows.length > 0 && (
        <>
          <Scatter rows={rows} params={active} axes={axes} sel={sel} onSel={i => { setSel(i); onPreview(rows[i]) }} />
          <div className="sl-row">
            <label>散布図</label>
            {[0, 1].map(k => (
              <select key={k} value={axes[k]} onChange={e => setAxes(a => (k ? [a[0], e.target.value] : [e.target.value, a[1]]) as [string, string])}>
                {active.map(p => <option key={p.id} value={p.id}>{p.name || p.key}</option>)}
              </select>
            ))}
          </div>
          <div className="sl-table-wrap">
            <table className="sl-table">
              <thead><tr><th>#</th>{active.map(p => <th key={p.id} title={p.name}>{p.key}</th>)}</tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className={sel === i ? 'sel' : ''} onClick={() => { setSel(i); onPreview(r) }}>
                    <td>{i + 1}</td>{active.map(p => <td key={p.id}>{fmt(r[p.id])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="sl-hint">点や行をクリックすると、その組み合わせの形を 3D で確認できます。</p>
        </>
      )}

      <h3>保存</h3>
      <div className="sl-row"><label>フォルダ名</label><input value={outName} onChange={e => setOutName(e.target.value)} /></div>
      <div className="sl-row">
        <label>形式</label>
        {formatsAvail.map(k => (
          <label key={k} className="sl-mini" title={FORMAT_HINT[k]}>
            <input type="checkbox" checked={formats[k] !== false} onChange={e => setFormats({ ...formats, [k]: e.target.checked })} />{k.toUpperCase()}
          </label>
        ))}
        <label className="sl-mini"><input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)} />上書き</label>
      </div>
      <div className="sl-btns">
        <button className="sl-btn sl-primary" disabled={!active.length || hasErrors || running || !outName}
          onClick={start}>{rows.length ? `この ${rows.length} 形状を作る` : `${doe.n} 形状を作る`}</button>
        {running && <button className="sl-btn" onClick={() => job && api.cancel(job.id)}>中止</button>}
      </div>
      {job && (
        <div className="sl-job">
          <div className="sl-bar"><div style={{ width: `${(100 * job.done) / Math.max(job.total, 1)}%` }} /></div>
          <div className="sl-sub">
            {job.done}/{job.total} 件（成功 {job.ok}・失敗 {job.failed}）
            {job.status === 'done' && ' — 完了'}{job.status === 'cancelled' && ' — 中止'}{job.status === 'error' && ` — エラー: ${job.error}`}
          </div>
          {jobRows.filter(r => r.status !== 'ok').map(r => <div key={r.sample} className="sl-err-s">✗ {r.sample}: {r.error}</div>)}
          {job.status !== 'running' && <div className="sl-path">保存先: <code>{jobPath}</code></div>}
        </div>
      )}

      {outputs.length > 0 && (
        <>
          <h3>作った形状</h3>
          <ul className="sl-outputs">
            {outputs.map(o => (
              <li key={o.name}>
                <span title={o.path}>{o.name}</span><span className="sl-sub">{o.samples} 形状</span>
                <a className="sl-link" href={api.zipUrl(o.name)}>zip</a>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function Scatter({ rows, params, axes, sel, onSel }: {
  rows: Record<string, number>[]; params: Param[]; axes: [string, string]; sel: number | null; onSel: (i: number) => void
}) {
  const px = params.find(p => p.id === axes[0]), py = params.find(p => p.id === axes[1])
  if (!px || !py) return null
  const W = 300, H = 200, M = 28
  const sx = (v: number) => M + ((v - px.min) / ((px.max - px.min) || 1)) * (W - M - 8)
  const sy = (v: number) => H - M - ((v - py.min) / ((py.max - py.min) || 1)) * (H - M - 8)
  return (
    <svg className="sl-scatter" viewBox={`0 0 ${W} ${H}`}>
      <rect x={M} y={8} width={W - M - 8} height={H - M - 8} className="sl-sc-bg" />
      <text x={(W + M) / 2} y={H - 6} textAnchor="middle">{px.name || px.key}</text>
      <text x={10} y={(H - M) / 2} textAnchor="middle" transform={`rotate(-90 10 ${(H - M) / 2})`}>{py.name || py.key}</text>
      <text x={M} y={H - M + 12} textAnchor="start" className="sl-sc-tick">{fmt(px.min)}</text>
      <text x={W - 8} y={H - M + 12} textAnchor="end" className="sl-sc-tick">{fmt(px.max)}</text>
      <text x={M - 3} y={H - M} textAnchor="end" className="sl-sc-tick">{fmt(py.min)}</text>
      <text x={M - 3} y={14} textAnchor="end" className="sl-sc-tick">{fmt(py.max)}</text>
      {rows.map((r, i) => (
        <circle key={i} cx={sx(r[px.id])} cy={sy(r[py.id])} r={sel === i ? 6 : 4}
          fill={sel === i ? PALETTE[1] : PALETTE[0]} fillOpacity={0.8} onClick={() => onSel(i)} style={{ cursor: 'pointer' }} />
      ))}
    </svg>
  )
}
