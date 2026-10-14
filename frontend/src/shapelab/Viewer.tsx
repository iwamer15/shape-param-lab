// three.js の 3D 表示。面ごとの色分け・面のクリック選択・元形状との重ね表示。
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { MeshData } from './types'

interface Props {
  mesh: MeshData | null
  ghost?: MeshData | null              // 元形状（半透明の線で重ねる）
  faceColors: Map<number, string>      // 面番号 → 色
  hoverFace?: number | null
  fitKey: string                       // 変わったときだけカメラを合わせ直す
  onPick?: (face: number, e: MouseEvent) => void
  onHover?: (face: number | null) => void
}

const BASE = new THREE.Color('#c3ccd6')
const HOVER = new THREE.Color('#ffd84a')

export function Viewer({ mesh, ghost, faceColors, hoverFace, fitKey, onPick, onHover }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const st = useRef<{
    renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera; controls: OrbitControls
    group: THREE.Group; ghostGroup: THREE.Group; solid?: THREE.Mesh; vFace?: Int32Array; data?: MeshData
  } | null>(null)
  const cb = useRef({ onPick, onHover })
  cb.current = { onPick, onHover }

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
    const group = new THREE.Group(), ghostGroup = new THREE.Group()
    scene.add(group, ghostGroup)
    st.current = { renderer, scene, camera, controls, group, ghostGroup }

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
    const pick = (e: MouseEvent): number | null => {
      const s = st.current
      if (!s?.solid || !s.data) return null
      const r = renderer.domElement.getBoundingClientRect()
      ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera)
      const hit = ray.intersectObject(s.solid)[0]
      if (!hit || hit.faceIndex == null) return null
      const f = s.data.faceIds[hit.faceIndex]
      return f >= 0 ? f : null
    }
    let down = { x: 0, y: 0 }
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY } }
    const onUp = (e: PointerEvent) => {
      if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4 || e.button !== 0) return  // ドラッグは回転
      const f = pick(e)
      if (f != null) cb.current.onPick?.(f, e)
    }
    let last: number | null = null
    const onMove = (e: PointerEvent) => {
      if (e.buttons) return
      const f = pick(e)
      if (f !== last) { last = f; cb.current.onHover?.(f) }
    }
    const onLeave = () => { last = null; cb.current.onHover?.(null) }
    renderer.domElement.addEventListener('pointerdown', onDown)
    renderer.domElement.addEventListener('pointerup', onUp)
    renderer.domElement.addEventListener('pointermove', onMove)
    renderer.domElement.addEventListener('pointerleave', onLeave)

    let raf = 0
    const loop = () => { controls.update(); renderer.render(scene, camera); raf = requestAnimationFrame(loop) }
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

  // 形状の更新
  useEffect(() => {
    const s = st.current
    if (!s) return
    clear(s.group)
    s.solid = undefined
    s.data = mesh ?? undefined
    if (!mesh) return
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(mesh.positions, 3))
    g.setIndex(new THREE.BufferAttribute(mesh.indices, 1))
    g.computeVertexNormals()
    // 頂点 → 面番号（OpenCascade は面ごとに頂点を持つので 1 頂点 = 1 面）
    const vFace = new Int32Array(mesh.positions.length / 3).fill(-1)
    for (let t = 0; t < mesh.faceIds.length; t++) {
      for (let k = 0; k < 3; k++) vFace[mesh.indices[t * 3 + k]] = mesh.faceIds[t]
    }
    g.setAttribute('color', new THREE.BufferAttribute(new Float32Array(mesh.positions.length), 3))
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
    s.controls.target.copy(c)
    s.camera.position.copy(c).add(new THREE.Vector3(1, -1.3, 0.9).normalize().multiplyScalar(r * 2.6))
    s.camera.near = r / 100
    s.camera.far = r * 100
    s.camera.updateProjectionMatrix()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey, mesh != null])

  return <div ref={host} className="sl-viewer" />
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
