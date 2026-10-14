import type { FaceInfo, Kind, Param } from './types'

export const PALETTE = ['#2f7de1', '#e8590c', '#2b9a66', '#c2255c', '#7048e8', '#0c8599', '#b08800', '#5c7cfa', '#d6336c', '#37b24d']

export const fmt = (v: number | null | undefined, d = 3) =>
  v == null || !isFinite(v) ? '—' : String(parseFloat(v.toFixed(d)))

const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const sub = (a: number[], b: number[]) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]]

/** face と平行な平面を距離順に（寸法の基準面の候補） */
export function parallelPlanes(faces: FaceInfo[], id: number) {
  const f = faces[id]
  if (!f || f.kind_code !== 0) return []
  return faces
    .filter(g => g.id !== id && g.kind_code === 0 && Math.abs(dot(g.normal, f.normal)) > 0.9998)
    .map(g => ({ face: g, dist: Math.abs(dot(sub(f.point, g.point), g.normal)) }))
    .filter(c => c.dist > 1e-6)
    .sort((a, b) => b.dist - a.dist)
}

/** 新しいパラメータの初期値（面の種類から推定） */
export function newParam(kind: Kind, faces: FaceInfo[], face: number | null, existing: Param[]): Param {
  const n = existing.length + 1
  let id = `p${n}`
  while (existing.some(p => p.id === id)) id = `p${Math.floor(Math.random() * 1e6)}`
  const prefix = kind === 'distance' ? 'dist' : kind === 'diameter' ? 'dia' : 'off'
  let key = `${prefix}_${n}`
  while (existing.some(p => p.key === key)) key += '_'
  const p: Param = { id, key, name: '', kind, faces: face == null ? [] : [{ face, coef: 1 }], ref_face: null, min: -2, max: 2, step: 0.5, enabled: true }
  if (face == null) return p
  const f = faces[face]
  if (kind === 'diameter' && f.radius) {
    const d = 2 * f.radius
    Object.assign(p, { min: roundTo(d * 0.8, 0.5), max: roundTo(d * 1.2, 0.5), name: f.convex ? `ボス外径 #${face}` : `穴径 #${face}` })
  } else if (kind === 'distance') {
    const c = parallelPlanes(faces, face)
    if (c.length) {
      // 既定の基準面: 反対向きの平面で最も近いもの（板厚・リブ幅・底面からの高さなど）
      const anti = [...c].reverse().find(x => dot(x.face.normal, f.normal) < 0) ?? c[0]
      p.ref_face = anti.face.id
      Object.assign(p, { min: roundTo(anti.dist * 0.8, 0.5), max: roundTo(anti.dist * 1.2, 0.5), name: `寸法 #${face}–#${anti.face.id}` })
    }
  } else if (kind === 'offset') {
    p.name = `面 #${face} の移動`
  }
  return p
}

const roundTo = (v: number, s: number) => Math.round(v / s) * s

export function stamp() {
  const d = new Date()
  const z = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}_${z(d.getHours())}${z(d.getMinutes())}`
}
