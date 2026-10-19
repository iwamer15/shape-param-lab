// three.js の 3D 表示。面ごとの色分け・面のクリック選択・元形状との重ね表示・寸法の矢印（つまみをドラッグして変形）。
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { Dim } from './dims'
import type { MeshData } from './types'

interface Props {
  mesh: MeshData | null
  ghost?: MeshData | null              // 元形状（半透明の線で重ねる）
  faceColors: Map<number, string>      // 面番号 → 色
  hoverFace?: number | null
  fitKey: string                       // 変わったときだけカメラを合わせ直す
  onPick?: (face: number, at: { x: number; y: number }) => void   // at: 3D 表示の中での位置（ポップアップ用）
  onMiss?: () => void                                               // 何もない所をクリック
  onHover?: (face: number | null) => void
  dims?: Dim[]
  activeDim?: string | null
  onDimDrag?: (id: string, value: number) => void
  onDimSelect?: (id: string) => void
}

const BASE = new THREE.Color('#c3ccd6')
const HOVER = new THREE.Color('#ffd84a')

export function Viewer({ mesh, ghost, faceColors, hoverFace, fitKey, onPick, onMiss, onHover, dims = [], activeDim,
  onDimDrag, onDimSelect }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const labelsEl = useRef<HTMLDivElement>(null)
  const st = useRef<{
    renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera; controls: OrbitControls
    group: THREE.Group; ghostGroup: THREE.Group; dimGroup: THREE.Group; solid?: THREE.Mesh; vFace?: Int32Array; data?: MeshData
    size: number; handles: THREE.Mesh[]; labels: { el: HTMLDivElement; pos: THREE.Vector3 }[]
  } | null>(null)
  const cb = useRef({ onPick, onMiss, onHover, onDimDrag, onDimSelect, dims })
  cb.current = { onPick, onMiss, onHover, onDimDrag, onDimSelect, dims }

  // 初期化
  useEffect(() => {
    const el = host.current!
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearColor('#f4f6f9')
    el.appendChild(renderer.domElement)
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 1e6)
    camera.up.set(0, 0, 1)
    camera.position.set(200, -250, 200)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    scene.add(new THREE.HemisphereLight('#ffffff', '#8a95a3', 1.6))
    const key = new THREE.DirectionalLight('#ffffff', 1.4)
    camera.add(key)
    key.position.set(0.5, 1, 0.3)
    scene.add(camera)
    const group = new THREE.Group(), ghostGroup = new THREE.Group(), dimGroup = new THREE.Group()
    scene.add(group, ghostGroup, dimGroup)
    st.current = { renderer, scene, camera, controls, group, ghostGroup, dimGroup, size: 100, handles: [], labels: [] }

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight
      renderer.setSize(w, h)
      camera.aspect = w / Math.max(h, 1)
      camera.updateProjectionMatrix()
    }
    const ro = new ResizeObserver(resize)
    ro.observe(el)
    resize()

    const ray = new THREE.Raycaster()
    const setRay = (e: MouseEvent) => {
      const r = renderer.domElement.getBoundingClientRect()
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera)
    }
    const pick = (e: MouseEvent): number | null => {
      const s = st.current
      if (!s?.solid || !s.data) return null
      setRay(e)
      const hit = ray.intersectObject(s.solid)[0]
      if (!hit || hit.faceIndex == null) return null
      const f = s.data.faceIds[hit.faceIndex]
      return f >= 0 ? f : null
    }
    const pickHandle = (e: MouseEvent): string | null => {
      const s = st.current
      if (!s?.handles.length) return null
      setRay(e)
      const hit = ray.intersectObjects(s.handles)[0]
      return hit ? (hit.object.userData.dim as string) : null
    }
    // つまみのドラッグ: マウスの光線と寸法の線の最も近い点 → 寸法の値
    let drag: string | null = null
    const dragTo = (e: PointerEvent) => {
      const d = cb.current.dims.find(x => x.id === drag)
      if (!d) return
      setRay(e)
      const o = new THREE.Vector3(...d.origin), u = new THREE.Vector3(...d.dir)
      const w0 = o.clone().sub(ray.ray.origin), v = ray.ray.direction
      const b = u.dot(v), dd = u.dot(w0), ee = v.dot(w0)
      const den = 1 - b * b
      if (Math.abs(den) < 1e-6) return            // 視線と寸法の向きが平行
      const sOnLine = (b * ee - dd) / den
      let val = d.toValue(sOnLine)
      val = Math.min(d.max, Math.max(d.min, Math.round(val / d.step) * d.step))
      cb.current.onDimDrag?.(d.id, parseFloat(val.toFixed(6)))
    }
    let down = { x: 0, y: 0 }
    const onDownCapture = (e: PointerEvent) => {
      if (e.button !== 0) return
      const h = pickHandle(e)
      if (h) {   // つまみを掴んだら回転させない
        drag = h
        controls.enabled = false
        renderer.domElement.setPointerCapture(e.pointerId)
        renderer.domElement.style.cursor = 'grabbing'
        cb.current.onDimSelect?.(h)
        e.stopImmediatePropagation()
      }
    }
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY } }
    const onUp = (e: PointerEvent) => {
      if (drag) {
        drag = null
        controls.enabled = true
        renderer.domElement.style.cursor = ''
        return
      }
      if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4 || e.button !== 0) return  // ドラッグは回転
      const f = pick(e)
      const r = el.getBoundingClientRect()
      if (f != null) cb.current.onPick?.(f, { x: e.clientX - r.left, y: e.clientY - r.top })
      else cb.current.onMiss?.()
    }
    let last: number | null = null
    const onMove = (e: PointerEvent) => {
      if (drag) { dragTo(e); return }
      if (e.buttons) return
      renderer.domElement.style.cursor = pickHandle(e) ? 'grab' : ''
      const f = pick(e)
      if (f !== last) { last = f; cb.current.onHover?.(f) }
    }
    const onLeave = () => { last = null; cb.current.onHover?.(null) }
    renderer.domElement.addEventListener('pointerdown', onDownCapture, { capture: true })
    renderer.domElement.addEventListener('pointerdown', onDown)
    renderer.domElement.addEventListener('pointerup', onUp)
    renderer.domElement.addEventListener('pointermove', onMove)
    renderer.domElement.addEventListener('pointerleave', onLeave)

    let raf = 0
    const tmp = new THREE.Vector3()
    const loop = () => {
      controls.update()
      renderer.render(scene, camera)
      // 寸法の文字を 3D の位置に合わせる
      const s = st.current
      if (s) {
        const w = renderer.domElement.clientWidth, h = renderer.domElement.clientHeight
        for (const lb of s.labels) {
          tmp.copy(lb.pos).project(camera)
          const vis = tmp.z < 1
          lb.el.style.display = vis ? '' : 'none'
          lb.el.style.transform = `translate(${((tmp.x + 1) / 2) * w}px, ${((1 - tmp.y) / 2) * h}px) translate(10px, -50%)`
        }
      }
      raf = requestAnimationFrame(loop)
    }
    loop()
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      controls.dispose()
      renderer.dispose()
      el.removeChild(renderer.domElement)
      st.current = null
    }
  }, [])

  // 寸法の矢印・つまみ・文字
  useEffect(() => {
    const s = st.current
    const box = labelsEl.current
    if (!s || !box) return
    clear(s.dimGroup)
    s.handles = []
    s.labels = []
    box.innerHTML = ''
    const rad = s.size * 0.011
    for (const d of dims) {
      const o = new THREE.Vector3(...d.origin), u = new THREE.Vector3(...d.dir)
      const a = o.clone().addScaledVector(u, d.from), b = o.clone().addScaledVector(u, d.to)
      const on = d.id === activeDim
      const col = new THREE.Color(d.color)
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([a, b]),
        new THREE.LineBasicMaterial({ color: col, depthTest: false, transparent: true, opacity: on ? 1 : 0.8 }))
      line.renderOrder = 10
      const tick = new THREE.Mesh(new THREE.SphereGeometry(rad * 0.45, 12, 8),
        new THREE.MeshBasicMaterial({ color: col, depthTest: false, transparent: true }))
      tick.position.copy(a)
      tick.renderOrder = 11
      const handle = new THREE.Mesh(new THREE.SphereGeometry(rad * (on ? 1.25 : 1), 20, 14),
        new THREE.MeshBasicMaterial({ color: col, depthTest: false, transparent: true }))
      handle.position.copy(b)
      handle.renderOrder = 12
      handle.userData.dim = d.id
      // 掴みやすいよう、見えない大きめの当たり判定
      const hit = new THREE.Mesh(new THREE.SphereGeometry(rad * 2.2, 10, 8), new THREE.MeshBasicMaterial({ visible: false }))
      hit.position.copy(b)
      hit.userData.dim = d.id
      s.dimGroup.add(line, tick, handle, hit)
      s.handles.push(hit)
      const el = document.createElement('div')
      el.className = `sl-dimlabel${on ? ' on' : ''}`
      el.style.borderColor = d.color
      // 寸法が多いと重なるので、選んでいないものは数値だけ（マウスを乗せると名前）
      el.textContent = on ? d.label : d.short
      el.title = d.label
      el.onmouseenter = () => { el.textContent = d.label; el.style.zIndex = '3' }
      el.onmouseleave = () => { if (!on) el.textContent = d.short; el.style.zIndex = '' }
      el.onclick = () => cb.current.onDimSelect?.(d.id)
      box.appendChild(el)
      s.labels.push({ el, pos: b })
    }
  }, [dims, activeDim])

  // 形状の更新
  useEffect(() => {
    const s = st.current
    if (!s) return
    clear(s.group)
    s.solid = undefined
    s.data = mesh ?? undefined
    if (!mesh) return
    // 頂点を「節点 × 面」ごとに分ける（STL・INP は面の境目で節点を共有しているので、
    // そのままだと色と陰影が隣の面ににじむ）。面の中はなめらか、境目はくっきりになる
    const { positions, indices, vFace } = splitByFace(mesh)
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    g.setIndex(new THREE.BufferAttribute(indices, 1))
    g.computeVertexNormals()
    g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(positions.length), 3))
    const solid = new THREE.Mesh(g, new THREE.MeshStandardMaterial({
      vertexColors: true, roughness: 0.55, metalness: 0.05, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
      side: THREE.DoubleSide,
    }))
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g, 25), new THREE.LineBasicMaterial({ color: '#2b3440' }))
    s.group.add(solid, edges)
    s.solid = solid
    s.vFace = vFace
    paint()
  }, [mesh])

  useEffect(() => {
    const s = st.current
    if (!s) return
    clear(s.ghostGroup)
    if (!ghost) return
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(ghost.positions, 3))
    g.setIndex(new THREE.BufferAttribute(ghost.indices, 1))
    s.ghostGroup.add(new THREE.LineSegments(new THREE.EdgesGeometry(g, 25),
      new THREE.LineBasicMaterial({ color: '#d0453b', transparent: true, opacity: 0.55, depthTest: false })))
  }, [ghost])

  // 色
  const paint = () => {
    const s = st.current
    if (!s?.solid || !s.vFace) return
    const attr = s.solid.geometry.getAttribute('color') as THREE.BufferAttribute
    const arr = attr.array as Float32Array
    const cache = new Map<number, THREE.Color>()
    const c = new THREE.Color()
    for (let v = 0; v < s.vFace.length; v++) {
      const f = s.vFace[v]
      let col = cache.get(f)
      if (!col) {
        const hex = faceColors.get(f)
        col = hex ? new THREE.Color(hex) : BASE.clone()
        if (f === hoverFace && f >= 0) col.lerp(HOVER, 0.6)
        cache.set(f, col)
      }
      c.copy(col)
      arr[v * 3] = c.r; arr[v * 3 + 1] = c.g; arr[v * 3 + 2] = c.b
    }
    attr.needsUpdate = true
  }
  useEffect(paint, [faceColors, hoverFace, mesh])

  // カメラ合わせ
  useEffect(() => {
    const s = st.current
    if (!s || !mesh) return
    const box = new THREE.Box3().setFromBufferAttribute(new THREE.BufferAttribute(mesh.positions, 3))
    const c = box.getCenter(new THREE.Vector3()), r = box.getSize(new THREE.Vector3()).length() / 2
    s.size = r * 2
    s.controls.target.copy(c)
    s.camera.position.copy(c).add(new THREE.Vector3(1, -1.3, 0.9).normalize().multiplyScalar(r * 2.6))
    s.camera.near = r / 100
    s.camera.far = r * 100
    s.camera.updateProjectionMatrix()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey, mesh != null])

  return (
    <div ref={host} className="sl-viewer">
      <div ref={labelsEl} className="sl-dimlabels" />
    </div>
  )
}

function splitByFace(mesh: MeshData) {
  const nT = mesh.faceIds.length
  const map = new Map<number, number>()
  const nV = mesh.positions.length / 3
  const pos: number[] = []
  const vf: number[] = []
  const idx = new Uint32Array(nT * 3)
  for (let t = 0; t < nT; t++) {
    const f = mesh.faceIds[t]
    for (let k = 0; k < 3; k++) {
      const v = mesh.indices[t * 3 + k]
      const key = (f + 1) * nV + v
      let n = map.get(key)
      if (n === undefined) {
        n = vf.length
        map.set(key, n)
        pos.push(mesh.positions[v * 3], mesh.positions[v * 3 + 1], mesh.positions[v * 3 + 2])
        vf.push(f)
      }
      idx[t * 3 + k] = n
    }
  }
  return { positions: new Float32Array(pos), indices: idx, vFace: Int32Array.from(vf) }
}

function clear(g: THREE.Group) {
  for (const o of [...g.children]) {
    g.remove(o)
    const m = o as THREE.Mesh
    m.geometry?.dispose()
    const mat = m.material as THREE.Material | undefined
    mat?.dispose()
  }
}
