// 形状パラメータ・ラボ（画面全体）。他のアプリには <ShapeLab apiBase="/api/shape" /> で埋め込める。
// 画面: 上に手順（① 読み込む → ② 変えたい所を選ぶ → ③ 範囲 → ④ 作る）、左に 3D、右に今の手順の内容。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './shapelab.css'
import { ShapeApi, type MorphInfo } from './api'
import { buildDims } from './dims'
import { DoePanel } from './DoePanel'
import { FacePopup } from './FacePopup'
import { ParamCard } from './ParamCard'
import { LoadStep, RangeStep, type Step, StepBar } from './Steps'
import { suggestionsFor } from './suggest'
import type { CheckResult, DoeSettings, MeshData, ModelInfo, Param, Project } from './types'
import { PALETTE, fmt, newParam } from './util'
import { Viewer } from './Viewer'

const DEFAULT_DOE: DoeSettings = { method: 'lhs', n: 20, seed: 0, levels: 3 }
const METHOD_LABEL: Record<string, string> = { none: '変形なし', offset: '精密（面オフセット）', boolean: '近似（押し出し/削り）', morph: 'メッシュ変形' }

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
  const [popup, setPopup] = useState<{ face: number; at: { x: number; y: number } } | null>(null)
  const [ghost, setGhost] = useState(true)
  const [method, setMethod] = useState('auto')
  const [projName, setProjName] = useState('')
  const [seg, setSeg] = useState(30)
  const [step, setStep] = useState<Step>(1)
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState('')
  const [adv, setAdv] = useState(false)
  const centerRef = useRef<HTMLElement>(null)

  const flash = (m: string) => { setMsg(m); window.setTimeout(() => setMsg(x => (x === m ? '' : x)), 4500) }
  const refreshLists = useCallback(() => {
    api.models().then(setModels).catch(e => flash(String(e.message)))
    api.projects().then(setProjects).catch(() => {})
  }, [api])

  useEffect(() => { api.meta().then(setMeta).catch(e => flash(`サーバに接続できません: ${e.message}`)); refreshLists() }, [api, refreshLists])

  const openModel = useCallback(async (name: string, pj?: Project, segAngle?: number) => {
    const sa = segAngle ?? pj?.seg_angle ?? 30
    setLoading(/\.(stl|inp)$/i.test(name) ? '形状を読み込み、面を見分けています…（大きいメッシュは数十秒）' : '形状を読み込んでいます…')
    setMorphErr('')
    try {
      const [inf, bm] = await Promise.all([api.info(name, sa), api.baseMesh(name, sa)])
      setModel(name); setInfo(inf); setBaseMesh(bm.mesh); setMesh(bm.mesh); setMorph(null); setSeg(sa)
      if (segAngle === undefined) { setParams(pj?.params ?? []); setDoe(pj?.doe ?? DEFAULT_DOE) }
      setValues({}); setActive(null); setPickMode(null); setPopup(null)
      setMethod(inf.methods[0] ?? 'auto')
      if (inf.model.warnings.length) flash(inf.model.warnings.join(' / '))
      return true
    } catch (e) { flash(String((e as Error).message)); return false }
    finally { setLoading('') }
  }, [api])

  const resegment = async (angle: number) => {
    if (params.length && !window.confirm('面の見分け方を変えると面の番号が変わり、作った寸法がずれることがあります。続けますか？')) return
    await openModel(model, undefined, angle)
  }
  const loadProject = async (name: string) => {
    try {
      const pj = await api.project(name)
      setProjName(name)
      if (await openModel(pj.model, pj)) setStep(2)
    } catch (e) { flash(String((e as Error).message)) }
  }
  const demo = async (kind: 'step' | 'stl' | 'fillet' = 'step') => {
    setLoading(kind === 'fillet' ? 'デモ形状を作り、面を見分けています…（20 秒ほど）' : 'デモ形状を作っています…')
    try {
      const r = await api.demo(kind)
      refreshLists(); setProjName(r.project)
      if (await openModel(r.name, r.data)) {
        setStep(2)
        flash('デモ形状と、寸法の例を読み込みました。矢印の先の ● をドラッグすると形が変わります')
      }
    } catch (e) { flash(String((e as Error).message)); setLoading('') }
  }
  const upload = async (f: File) => {
    setLoading(`${f.name} を送っています…`)
    try {
      const r = await api.upload(f)
      refreshLists(); setProjName('')
      if (await openModel(r.name)) { setStep(2); flash('読み込みました。変えたい面をクリックしてください') }
    } catch (e) { flash(String((e as Error).message)); setLoading('') }
  }
  const isMesh = info?.model.type === 'mesh'
  const project = useCallback((): Project => ({
    version: 1, model, params, doe: { ...doe, constraints: (doe.constraints ?? []).map(c => c.trim()).filter(Boolean) },
    ...(isMesh ? { seg_angle: seg } : {}),
  }), [model, params, doe, isMesh, seg])
  const save = async () => {
    const name = projName.trim() || model.replace(/\.[^.]+$/, '')
    try { await api.saveProject(name, project()); setProjName(name); refreshLists(); flash(`設定を保存しました: ${name}`) } catch (e) { flash(String((e as Error).message)) }
  }

  // 寸法の検証（元の値・エラー）
  useEffect(() => {
    if (!model) return
    const t = window.setTimeout(() => {
      api.check(model, params, seg).then(r => setChecks(Object.fromEntries(r.map(c => [c.id, c])))).catch(() => {})
    }, 120)
    return () => window.clearTimeout(t)
  }, [api, model, params, seg])

  // 変形（値が変わるたびに、少し待ってからサーバで計算）
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
        const r = await api.morph(model, ok, vals, method, seg, ac.signal)
        setMesh(r.mesh); setMorph(r.info); setMorphErr('')
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setMorphErr(String((e as Error).message))
      }
      if (abort.current === ac) setComputing(false)
    }, 150)
    return () => window.clearTimeout(t)
  }, [api, model, info, params, values, checks, method, baseMesh, seg])

  // 面の色: 寸法ごとの色。測る基準の面は薄く
  const faceColors = useMemo(() => {
    const m = new Map<number, string>()
    params.forEach((p, i) => {
      const c = PALETTE[i % PALETTE.length]
      if (p.ref_face != null && p.kind === 'distance' && (active === p.id || !active)) m.set(p.ref_face, mix(c, '#c3ccd6', 0.55))
      for (const f of p.faces) m.set(f.face, c)
    })
    if (popup && !m.has(popup.face)) m.set(popup.face, '#ffd84a')
    return m
  }, [params, active, popup])

  const dims = useMemo(() => (info ? buildDims(params, info.faces, checks, values) : []), [params, info, checks, values])

  const setValue = (id: string, v: number | undefined) =>
    setValues(vs => { const n = { ...vs }; if (v === undefined || isNaN(v)) delete n[id]; else n[id] = v; return n })
  const updateParam = (p: Param) => setParams(ps => ps.map(q => (q.id === p.id ? p : q)))
  const addParam = (p: Param) => {
    setParams(ps => [...ps, p]); setActive(p.id); setPopup(null)
    if (step !== 2) setStep(2)
    flash('寸法を作りました。右のスライダーか、3D の矢印の先の ● をドラッグして形を変えられます')
  }
  const removeParam = (id: string) => {
    setParams(ps => ps.filter(q => q.id !== id)); setValue(id, undefined)
    if (active === id) setActive(null)
  }
  const onPick = (face: number, at: { x: number; y: number }) => {
    const p = params.find(q => q.id === active)
    if (p && pickMode && info) {        // 詳細設定から「面を足す」「基準の面を選ぶ」を押しているとき
      if (pickMode === 'faces') {
        const has = p.faces.some(f => f.face === face)
        const next = { ...p, faces: has ? p.faces.filter(f => f.face !== face) : [...p.faces, { face, coef: 1 }] }
        if (!has && p.faces.length === 0) {
          const d = newParam(p.kind, info.faces, face, params.filter(q => q.id !== p.id))
          Object.assign(next, { ref_face: p.ref_face ?? d.ref_face, min: d.min, max: d.max, name: p.name || d.name })
        }
        updateParam(next)
      } else {
        updateParam({ ...p, ref_face: face }); setPickMode('faces')
      }
      return
    }
    if (step === 1 || step === 3 || step === 4) setStep(2)
    setPopup({ face, at })
  }

  const hasErrors = params.some(p => p.enabled !== false && checks[p.id]?.error)
  const hoverInfo = hover != null ? info?.faces[hover] : null
  const dv = morph && info ? (morph.mass.volume / info.mass.volume - 1) * 100 : null
  const suggestions = popup && info ? suggestionsFor(info.faces[popup.face], info.faces, params) : null
  const box = centerRef.current ? { w: centerRef.current.clientWidth, h: centerRef.current.clientHeight } : { w: 800, h: 600 }

  return (
    <div className="sl-root">
      <header className="sl-top">
        <div className="sl-brand">形状パラメータ・ラボ</div>
        <StepBar step={step} onStep={setStep}
          enabled={n => n === 1 || (!!model && (n === 2 || params.length > 0))}
          done={n => (n === 1 && !!model) || (n === 2 && params.length > 0)} />
        {model && (
          <div className="sl-save">
            <input value={projName} placeholder="設定の名前" onChange={e => setProjName(e.target.value)} />
            <button className="sl-btn" onClick={save} title="寸法と範囲の設定を保存して、あとで続きから使えるようにします">設定を保存</button>
          </div>
        )}
      </header>

      <div className="sl-body">
        <main className="sl-center" ref={centerRef}>
          <Viewer mesh={mesh} ghost={ghost && morph ? baseMesh : null} faceColors={faceColors} hoverFace={hover}
            fitKey={model} onPick={onPick} onMiss={() => setPopup(null)} onHover={setHover}
            dims={step === 1 ? [] : dims} activeDim={active}
            onDimDrag={(id, v) => setValue(id, v)} onDimSelect={id => { setActive(id); setPopup(null) }} />
          {!model && !loading && (
            <div className="sl-empty">
              <h1>形状パラメータ・ラボ</h1>
              <p>形状（CAD・STL・INP）を読み込み、<b>変えたい所をクリック</b>して寸法を作り、
                <b>矢印をドラッグ</b>して形を変えます。最後に、いろいろな寸法の形状をまとめて作れます（サロゲートの学習データ用）。</p>
              <div className="sl-btns" style={{ justifyContent: 'center' }}>
                <button className="sl-btn sl-primary" onClick={() => demo('step')}>デモで試す</button>
              </div>
              <p className="sl-sub">自分のファイルは、右の枠にドロップしてください。</p>
            </div>
          )}
          {model && (
            <div className="sl-hud">
              <div className="sl-hud-row">
                <b>{model}</b>
                {info && <span>体積 {fmt((morph?.mass.volume ?? info.mass.volume) / 1000, 1)} cm³
                  {dv != null && Math.abs(dv) > 1e-6 && <b className={dv > 0 ? 'sl-up' : 'sl-down'}> （{dv > 0 ? '+' : ''}{fmt(dv, 2)}%）</b>}</span>}
                {computing && <span className="sl-spin">計算中…</span>}
                {morph && Object.keys(values).length > 0 && <button className="sl-link" onClick={() => setValues({})}>すべて元の形に戻す</button>}
              </div>
              {step === 2 && !popup && !pickMode && (
                <div className="sl-guide">👆 変えたい面をクリック{params.length > 0 && '　／　矢印の先の ● をドラッグすると形が変わります'}</div>
              )}
              {pickMode && <div className="sl-pick">🖱 {pickMode === 'faces' ? '一緒に動かす面' : '測る基準の面'}をクリックしてください
                <button className="sl-link" onClick={() => setPickMode(null)}>終わる</button></div>}
              {morphErr && <div className="sl-err">⚠ この形は作れません: {morphErr}</div>}
            </div>
          )}
          {suggestions && popup && (
            <FacePopup s={suggestions} at={popup.at} box={box} onChoose={addParam}
              onEdit={id => { setActive(id); setPopup(null) }} onClose={() => setPopup(null)} />
          )}
          {hoverInfo && !popup && <div className="sl-tip">{hoverInfo.label}</div>}
          {model && (
            <label className="sl-ghost sl-mini"><input type="checkbox" checked={ghost} onChange={e => setGhost(e.target.checked)} />元の形を赤い線で重ねる</label>
          )}
          {(loading || msg) && <div className="sl-toast">{loading || msg}</div>}
        </main>

        <aside className="sl-panel">
          {step === 1 && (
            <LoadStep models={models} projects={projects} model={model} info={info} busy={!!loading}
              onUpload={upload} onOpen={async n => { if (await openModel(n)) setStep(2) }} onProject={loadProject} onDemo={demo}
              seg={seg} onResegment={resegment} onNext={() => setStep(2)} />
          )}
          {step === 2 && model && (
            <div className="sl-stepbody">
              <h2>② 変えたい所を選びます</h2>
              <p className="sl-hint">3D の形の中で、<b>変えたい面をクリック</b>してください（穴・ボス・上面・リブの側面など）。
                出てくる候補から選ぶと寸法ができます。</p>
              {params.length === 0 && <div className="sl-emptybox">まだ寸法がありません。左の 3D の面をクリックしてみてください。</div>}
              {params.map((p, i) => (
                <ParamCard key={p.id} p={p} color={PALETTE[i % PALETTE.length]} check={checks[p.id]} value={values[p.id]}
                  faces={info?.faces ?? []} kinds={meta?.kinds ?? {}} active={active === p.id}
                  pickMode={active === p.id ? pickMode : null}
                  onSelect={() => { if (active !== p.id) { setActive(p.id); setPickMode(null) } }}
                  onChange={updateParam} onValue={v => setValue(p.id, v)}
                  onPick={m => { setActive(p.id); setPickMode(m) }} onDelete={() => removeParam(p.id)} />
              ))}
              {params.length > 0 && (
                <button className="sl-btn sl-primary sl-next" disabled={hasErrors} onClick={() => setStep(3)}>次へ: 範囲を決める →</button>
              )}
              {!isMesh && model && (
                <div className="sl-adv">
                  <button className="sl-link" onClick={() => setAdv(!adv)}>{adv ? '▾' : '▸'} 形の作り方（詳細）</button>
                  {adv && (
                    <>
                      <div className="sl-row">
                        <label>方式</label>
                        <select value={method} onChange={e => setMethod(e.target.value)}>
                          <option value="auto">自動（精密 → だめなら近似）</option>
                          <option value="offset">精密のみ</option>
                          <option value="boolean">近似のみ</option>
                        </select>
                      </div>
                      {morph && <p className="sl-hint">今の形: {METHOD_LABEL[morph.method]}（{fmt(morph.seconds, 2)} 秒）</p>}
                    </>
                  )}
                </div>
              )}
              {isMesh && morph?.quality?.min_volume_ratio != null && (
                <p className="sl-hint">要素のつぶれ具合: いちばん小さい体積比 {fmt(morph.quality.min_volume_ratio, 2)}
                  （1 が元のまま。0 に近いほどつぶれている）{!morph.full && '　※大きなメッシュのため、操作中は表面だけ動かしています'}</p>
              )}
            </div>
          )}
          {step === 3 && model && (
            <RangeStep api={api} model={model} seg={seg} morphMethod={method} params={params} checks={checks}
              doe={doe} onDoe={setDoe} onChange={updateParam} onNext={() => setStep(4)} />
          )}
          {step === 4 && model && meta && (
            <DoePanel api={api} model={model} params={params} doe={doe} methods={meta.doe_methods} morphMethod={method}
              seg={seg} formatsAvail={info?.formats ?? ['stl']}
              hasErrors={hasErrors} onDoe={setDoe} project={project}
              onPreview={v => setValues(Object.fromEntries(Object.entries(v)))} />
          )}
          {meta && <p className="sl-foot">保存先: <code>{meta.data_dir}</code></p>}
        </aside>
      </div>
    </div>
  )
}

function mix(a: string, b: string, t: number) {
  const p = (h: string) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16))
  const x = p(a), y = p(b)
  return '#' + x.map((v, i) => Math.round(v * (1 - t) + y[i] * t).toString(16).padStart(2, '0')).join('')
}
