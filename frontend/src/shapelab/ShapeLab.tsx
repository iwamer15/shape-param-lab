// 形状パラメータ・ラボ（画面全体）。他のアプリには <ShapeLab apiBase="/api/shape" /> で埋め込める。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './shapelab.css'
import { ShapeApi, type MorphInfo } from './api'
import { DoePanel } from './DoePanel'
import { ParamCard } from './ParamCard'
import type { CheckResult, DoeSettings, Kind, MeshData, ModelInfo, Param, Project } from './types'
import { PALETTE, fmt, newParam } from './util'
import { Viewer } from './Viewer'

const DEFAULT_DOE: DoeSettings = { method: 'lhs', n: 20, seed: 0, levels: 3 }
const METHOD_LABEL: Record<string, string> = { none: '変形なし', offset: '精密（面オフセット）', boolean: '近似（押し出し/削り）' }

export function ShapeLab({ apiBase = '/api/shape' }: { apiBase?: string }) {
  const api = useMemo(() => new ShapeApi(apiBase), [apiBase])
  const [meta, setMeta] = useState<{ kinds: Record<string, string>; doe_methods: Record<string, string>; data_dir: string } | null>(null)
  const [models, setModels] = useState<{ name: string }[]>([])
  const [projects, setProjects] = useState<{ name: string }[]>([])
  const [model, setModel] = useState('')
  const [info, setInfo] = useState<ModelInfo | null>(null)
  const [baseMesh, setBaseMesh] = useState<MeshData | null>(null)
  const [mesh, setMesh] = useState<MeshData | null>(null)
  const [morph, setMorph] = useState<MorphInfo | null>(null)
  const [morphErr, setMorphErr] = useState('')
  const [computing, setComputing] = useState(false)
  const [params, setParams] = useState<Param[]>([])
  const [values, setValues] = useState<Record<string, number>>({})
  const [checks, setChecks] = useState<Record<string, CheckResult>>({})
  const [doe, setDoe] = useState<DoeSettings>(DEFAULT_DOE)
  const [active, setActive] = useState<string | null>(null)
  const [pickMode, setPickMode] = useState<'faces' | 'ref' | null>(null)
  const [hover, setHover] = useState<number | null>(null)
  const [selFace, setSelFace] = useState<number | null>(null)
  const [ghost, setGhost] = useState(true)
  const [method, setMethod] = useState('auto')
  const [projName, setProjName] = useState('')
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const flash = (m: string) => { setMsg(m); window.setTimeout(() => setMsg(x => (x === m ? '' : x)), 4000) }
  const refreshLists = useCallback(() => {
    api.models().then(setModels).catch(e => flash(String(e.message)))
    api.projects().then(setProjects).catch(() => {})
  }, [api])

  useEffect(() => { api.meta().then(setMeta).catch(e => flash(`サーバに接続できません: ${e.message}`)); refreshLists() }, [api, refreshLists])

  const openModel = useCallback(async (name: string, pj?: Project) => {
    setLoading('モデルを読み込み中…'); setMorphErr('')
    try {
      const [inf, bm] = await Promise.all([api.info(name), api.baseMesh(name)])
      setModel(name); setInfo(inf); setBaseMesh(bm.mesh); setMesh(bm.mesh); setMorph(null)
      setParams(pj?.params ?? []); setDoe(pj?.doe ?? DEFAULT_DOE); setValues({}); setActive(null); setPickMode(null); setSelFace(null)
    } catch (e) { flash(String((e as Error).message)) }
    setLoading('')
  }, [api])

  const loadProject = async (name: string) => {
    try {
      const pj = await api.project(name)
      setProjName(name)
      await openModel(pj.model, pj)
    } catch (e) { flash(String((e as Error).message)) }
  }
  const demo = async () => {
    setLoading('デモ形状を作成中…')
    try {
      const r = await api.demo()
      refreshLists(); setProjName(r.project)
      await openModel(r.name, r.data)
      flash('デモ形状（板＋ボス2個＋穴＋リブ＋窓）と寸法 7 個を読み込みました')
    } catch (e) { flash(String((e as Error).message)) }
    setLoading('')
  }
  const upload = async (f: File) => {
    setLoading(`${f.name} を読み込み中…`)
    try { const r = await api.upload(f); refreshLists(); setProjName(''); await openModel(r.name) } catch (e) { flash(String((e as Error).message)) }
    setLoading('')
  }
  const project = useCallback((): Project => ({ version: 1, model, params, doe }), [model, params, doe])
  const save = async () => {
    const name = projName.trim() || model.replace(/\.[^.]+$/, '')
    try { await api.saveProject(name, project()); setProjName(name); refreshLists(); flash(`保存しました: ${name}`) } catch (e) { flash(String((e as Error).message)) }
  }

  // パラメータの検証（元の値・エラー）
  useEffect(() => {
    if (!model) return
    const t = window.setTimeout(() => {
      api.check(model, params).then(r => setChecks(Object.fromEntries(r.map(c => [c.id, c])))).catch(() => {})
    }, 150)
    return () => window.clearTimeout(t)
  }, [api, model, params])

  // 変形（スライダーを動かすたびに、少し待ってからサーバで計算）
  const abort = useRef<AbortController | null>(null)
  useEffect(() => {
    if (!model || !info) return
    const ok = params.filter(p => !checks[p.id]?.error && checks[p.id])
    const vals = Object.fromEntries(Object.entries(values).filter(([k]) => ok.some(p => p.id === k)))
    if (!Object.keys(vals).length) { setMesh(baseMesh); setMorph(null); setMorphErr(''); return }
    const t = window.setTimeout(async () => {
      abort.current?.abort()
      const ac = new AbortController()
      abort.current = ac
      setComputing(true)
      try {
        const r = await api.morph(model, ok, vals, method, ac.signal)
        setMesh(r.mesh); setMorph(r.info); setMorphErr('')
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setMorphErr(String((e as Error).message))
      }
      if (abort.current === ac) setComputing(false)
    }, 180)
    return () => window.clearTimeout(t)
  }, [api, model, info, params, values, checks, method, baseMesh])

  // 面の色: パラメータごとの色。基準面は薄く
  const colors = useMemo(() => {
    const m = new Map<number, string>()
    params.forEach((p, i) => {
      const c = PALETTE[i % PALETTE.length]
      if (p.ref_face != null && p.kind === 'distance' && (active === p.id || !active)) m.set(p.ref_face, c + '66')
      for (const f of p.faces) m.set(f.face, c)
    })
    if (selFace != null && !m.has(selFace)) m.set(selFace, '#9aa7b6')
    return m
  }, [params, active, selFace])
  // three.js の Color は 8 桁 hex を読めないので、薄い色は混ぜて作る
  const faceColors = useMemo(() => {
    const out = new Map<number, string>()
    colors.forEach((v, k) => out.set(k, v.length === 9 ? mix(v.slice(0, 7), '#c3ccd6', 0.55) : v))
    return out
  }, [colors])

  const updateParam = (p: Param) => setParams(ps => ps.map(q => (q.id === p.id ? p : q)))
  const addParam = (kind: Kind, face: number | null) => {
    if (!info) return
    const p = newParam(kind, info.faces, face, params)
    setParams(ps => [...ps, p]); setActive(p.id); setPickMode(face == null ? 'faces' : null)
  }
  const onPick = (face: number) => {
    setSelFace(face)
    const p = params.find(q => q.id === active)
    if (!p || !pickMode) return
    if (pickMode === 'faces') {
      const has = p.faces.some(f => f.face === face)
      const next = { ...p, faces: has ? p.faces.filter(f => f.face !== face) : [...p.faces, { face, coef: 1 }] }
      // 1 枚目を選んだときは種類に合わせて初期値（基準面・範囲・名前）を入れる
      if (!has && p.faces.length === 0 && info) {
        const d = newParam(p.kind, info.faces, face, params.filter(q => q.id !== p.id))
        Object.assign(next, { ref_face: p.ref_face ?? d.ref_face, min: d.min, max: d.max, name: p.name || d.name })
      }
      updateParam(next)
    } else {
      updateParam({ ...p, ref_face: face }); setPickMode('faces')
    }
  }

  const hasErrors = params.some(p => p.enabled !== false && checks[p.id]?.error)
  const face = selFace != null ? info?.faces[selFace] : null
  const hoverInfo = hover != null ? info?.faces[hover] : null
  const dv = morph && info ? (morph.mass.volume / info.mass.volume - 1) * 100 : null

  return (
    <div className="sl-root">
      <aside className="sl-left">
        <section>
          <h2>モデル</h2>
          <div className="sl-row">
            <select value={model} onChange={e => e.target.value && openModel(e.target.value)}>
              <option value="">（CAD を選択）</option>
              {models.map(m => <option key={m.name} value={m.name}>{m.name}</option>)}
            </select>
          </div>
          <div className="sl-btns">
            <button className="sl-btn" onClick={() => fileRef.current?.click()}>CAD を開く（STEP / IGES）</button>
            <button className="sl-btn" onClick={demo}>デモ形状</button>
            <input ref={fileRef} type="file" hidden accept=".step,.stp,.iges,.igs,.brep,.brp"
              onChange={e => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
          </div>
          <div className="sl-row">
            <select value="" onChange={e => e.target.value && loadProject(e.target.value)}>
              <option value="">保存したパラメータ定義を開く…</option>
              {projects.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
            </select>
          </div>
          {model && (
            <div className="sl-row">
              <input value={projName} placeholder="定義の名前" onChange={e => setProjName(e.target.value)} />
              <button className="sl-btn" onClick={save}>保存</button>
            </div>
          )}
        </section>

        {model && (
          <section className="sl-params">
            <h2>寸法パラメータ <span className="sl-sub">{params.length} 個</span></h2>
            {params.length === 0 && <p className="sl-hint">3D で面をクリック → 右の「選択中の面」から寸法を作るか、下のボタンで追加します。</p>}
            {params.map((p, i) => (
              <ParamCard key={p.id} p={p} color={PALETTE[i % PALETTE.length]} check={checks[p.id]} value={values[p.id]}
                faces={info?.faces ?? []} kinds={meta?.kinds ?? {}} active={active === p.id}
                pickMode={active === p.id ? pickMode : null}
                onSelect={() => { if (active !== p.id) { setActive(p.id); setPickMode(null) } }}
                onChange={updateParam}
                onValue={v => setValues(vs => { const n = { ...vs }; if (v === undefined || isNaN(v)) delete n[p.id]; else n[p.id] = v; return n })}
                onPick={m => { setActive(p.id); setPickMode(m) }}
                onDelete={() => { setParams(ps => ps.filter(q => q.id !== p.id)); setValues(vs => { const n = { ...vs }; delete n[p.id]; return n }); if (active === p.id) setActive(null) }}
              />
            ))}
            <div className="sl-btns">
              <button className="sl-btn" onClick={() => addParam('distance', null)}>＋ 寸法（距離）</button>
              <button className="sl-btn" onClick={() => addParam('diameter', null)}>＋ 直径</button>
              <button className="sl-btn" onClick={() => addParam('offset', null)}>＋ 面の移動</button>
            </div>
            {Object.keys(values).length > 0 && <button className="sl-link" onClick={() => setValues({})}>すべて元の寸法に戻す</button>}
          </section>
        )}
      </aside>

      <main className="sl-center">
        <Viewer mesh={mesh} ghost={ghost && morph ? baseMesh : null} faceColors={faceColors} hoverFace={hover}
          fitKey={model} onPick={onPick} onHover={setHover} />
        {!model && !loading && (
          <div className="sl-empty">
            <h1>形状パラメータ・ラボ</h1>
            <p>CAD（STEP / IGES）を読み込み、面を選んで <b>寸法をパラメータ化</b> → スライダーで形を変えて確認 →
              実験計画（LHS など）で <b>サロゲート学習用の形状を一括生成</b> します。</p>
            <div className="sl-btns">
              <button className="sl-btn sl-primary" onClick={demo}>デモ形状で試す</button>
              <button className="sl-btn" onClick={() => fileRef.current?.click()}>CAD を開く</button>
            </div>
          </div>
        )}
        {model && (
          <div className="sl-hud">
            <div className="sl-hud-row">
              <b>{model}</b>
              {info && <span>面 {info.faces.length} 枚</span>}
              {info && <span>体積 {fmt((morph?.mass.volume ?? info.mass.volume) / 1000, 1)} cm³
                {dv != null && Math.abs(dv) > 1e-6 && <b className={dv > 0 ? 'sl-up' : 'sl-down'}> （{dv > 0 ? '+' : ''}{fmt(dv, 2)}%）</b>}</span>}
              {morph && <span className={`sl-badge ${morph.method}`} title={morph.method === 'boolean'
                ? '面オフセットで作れなかったため、押し出し・削り（ブーリアン）で作りました。隣の面は動かす面の法線方向にまっすぐ延びます（抜き勾配は保たれません）。'
                : '隣の面が自然に伸び縮みする方式で作りました。'}>{METHOD_LABEL[morph.method]}</span>}
              {morph && <span className="sl-sub">{fmt(morph.seconds, 2)} 秒</span>}
              {computing && <span className="sl-spin">計算中…</span>}
            </div>
            <div className="sl-hud-row">
              <label className="sl-mini"><input type="checkbox" checked={ghost} onChange={e => setGhost(e.target.checked)} />元の形を赤線で重ねる</label>
              <label className="sl-mini">方式
                <select value={method} onChange={e => setMethod(e.target.value)}>
                  <option value="auto">自動（精密→だめなら近似）</option>
                  <option value="offset">精密のみ</option>
                  <option value="boolean">近似のみ</option>
                </select>
              </label>
            </div>
            {pickMode && <div className="sl-pick">🖱 {pickMode === 'faces' ? '動かす面' : '基準面'}をクリックしてください（もう一度クリックで解除）
              <button className="sl-link" onClick={() => setPickMode(null)}>終了</button></div>}
            {morphErr && <div className="sl-err">⚠ {morphErr}</div>}
          </div>
        )}
        {hoverInfo && <div className="sl-tip">{hoverInfo.label}</div>}
        {(loading || msg) && <div className="sl-toast">{loading || msg}</div>}
      </main>

      <aside className="sl-right">
        {model && (
          <section>
            <h2>選択中の面</h2>
            {!face && <p className="sl-hint">3D の面をクリックすると、種類・寸法が表示されます。</p>}
            {face && (
              <div className="sl-face">
                <div><b>{face.label}</b></div>
                <div className="sl-sub">面積 {fmt(face.area, 1)} mm² ／ 中心 ({face.center.map(v => fmt(v, 1)).join(', ')})</div>
                {face.kind_code === 0 && <div className="sl-sub">法線 ({face.normal.map(v => fmt(v, 3)).join(', ')})</div>}
                {face.axis_dir && <div className="sl-sub">軸 ({face.axis_dir.map(v => fmt(v, 3)).join(', ')})</div>}
                <div className="sl-btns">
                  {face.kind_code === 0 && <button className="sl-btn sl-btn-s" onClick={() => addParam('distance', face.id)}>この面の寸法（距離）を作る</button>}
                  {face.kind_code === 1 && <button className="sl-btn sl-btn-s" onClick={() => addParam('diameter', face.id)}>この円筒の直径を作る</button>}
                  <button className="sl-btn sl-btn-s" onClick={() => addParam('offset', face.id)}>この面の移動量を作る</button>
                </div>
              </div>
            )}
          </section>
        )}
        {model && meta && (
          <section>
            <DoePanel api={api} model={model} params={params} doe={doe} methods={meta.doe_methods} morphMethod={method}
              hasErrors={hasErrors} onDoe={setDoe} project={project}
              onPreview={v => setValues(Object.fromEntries(Object.entries(v)))} />
          </section>
        )}
        {meta && <p className="sl-foot">データ保存先: <code>{meta.data_dir}</code></p>}
      </aside>
    </div>
  )
}

function mix(a: string, b: string, t: number) {
  const p = (h: string) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16))
  const x = p(a), y = p(b)
  return '#' + x.map((v, i) => Math.round(v * (1 - t) + y[i] * t).toString(16).padStart(2, '0')).join('')
}
