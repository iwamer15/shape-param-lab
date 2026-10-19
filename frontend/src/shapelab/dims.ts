// 寸法を 3D 上に描くための形（矢印の始点・向き・つまみの位置）と、つまみの位置 ⇔ 寸法の値 の変換
import type { CheckResult, FaceInfo, Param } from './types'
import { PALETTE, fmt } from './util'

export type V3 = [number, number, number]

export interface Dim {
  id: string
  color: string
  origin: V3         // 測り始めの点（元の形の上）
  dir: V3            // 測る向き（単位ベクトル）
  from: number       // 矢印の始点（origin からの距離）
  to: number         // つまみの位置（origin からの距離）
  label: string      // 名前つき（選んだとき）
  short: string      // 数値だけ（ふだん）
  value: number
  toValue: (s: number) => number   // つまみの位置 → 寸法の値
  min: number
  max: number
  step: number
}

const sub = (a: number[], b: number[]): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
const add = (a: number[], b: number[], k = 1): V3 => [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k]
const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const len = (a: number[]) => Math.hypot(a[0], a[1], a[2])
const unit = (a: number[]): V3 => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l] }

export function buildDims(params: Param[], faces: FaceInfo[], checks: Record<string, CheckResult>,
  values: Record<string, number>): Dim[] {
  const out: Dim[] = []
  params.forEach((p, i) => {
    const base = checks[p.id]?.base
    if (base == null || checks[p.id]?.error || !p.faces.length) return
    const v = values[p.id] ?? base
    const color = PALETTE[i % PALETTE.length]
    const common = { id: p.id, color, value: v, min: p.min, max: p.max, step: p.step && p.step > 0 ? p.step : 0.1 }
    if (p.kind === 'distance' && p.ref_face != null) {
      const r = faces[p.ref_face]
      const mv = p.faces.find(f => f.face !== p.ref_face)
      if (!r || !mv) return
      const m = faces[mv.face]
      const cm = mv.coef
      const cr = p.faces.find(f => f.face === p.ref_face)?.coef ?? 0
      const o = add(m.point, r.normal, -dot(sub(m.point, r.point), r.normal))   // 動かす面の点を基準面へ落とした点
      let dir = sub(m.point, o)
      dir = len(dir) > 1e-9 ? unit(dir) : unit(m.normal)
      const k = v - base
      out.push({ ...common, origin: o, dir, from: -cr * k, to: base + cm * k,
        label: `${p.name || p.key}  ${fmt(v, 2)} mm`, short: `${fmt(v, 2)}`, toValue: s => base + (s - base) / (cm || 1) })
    } else if (p.kind === 'diameter') {
      const f = faces[p.faces[0].face]
      if (!f?.axis_loc || !f.axis_dir) return
      const c = add(f.axis_loc, f.axis_dir, dot(sub(f.point, f.axis_loc), f.axis_dir))   // 軸上の点
      const rel = sub(f.point, c)
      const rp = len(rel)
      if (rp < 1e-9) return
      const coef = p.faces[0].coef || 1
      out.push({ ...common, origin: c, dir: unit(rel), from: 0, to: rp + coef * (v - base) / 2,
        label: `${p.name || p.key}  φ${fmt(v, 2)}`, short: `φ${fmt(v, 2)}`, toValue: s => base + (2 * (s - rp)) / coef })
    } else if (p.kind === 'offset') {
      const f = faces[p.faces[0].face]
      if (!f) return
      const coef = p.faces[0].coef || 1
      out.push({ ...common, origin: f.point as V3, dir: unit(f.normal), from: 0, to: coef * v,
        label: `${p.name || p.key}  ${v >= 0 ? '+' : ''}${fmt(v, 2)} mm`, short: `${v >= 0 ? '+' : ''}${fmt(v, 2)}`, toValue: s => s / coef })
    }
  })
  return out
}
