// クリックした面から「どう変えられるか」の候補を、言葉で作る（寸法の種類・基準面・係数を利用者が選ばなくてよいように）
import type { FaceInfo, Kind, Param } from './types'
import { fmt, newParam, parallelPlanes } from './util'

export interface Suggestion {
  title: string        // 例: この穴の大きさ（直径）を変える
  detail: string       // 例: いま φ12 mm
  make: () => Param
}

export interface FaceSuggestions {
  face: FaceInfo
  what: string                 // 例: 穴（円筒面）
  usedBy: Param[]              // この面を使っている寸法
  notes: string[]
  items: Suggestion[]
  more: Suggestion[]           // 基準にする面を選び直す など
}

const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const r05 = (v: number) => Math.round(v * 2) / 2

function range(base: number, rel = 0.2, minAbs = 1) {
  const w = Math.max(Math.abs(base) * rel, minAbs)
  return { min: r05(base - w), max: r05(base + w) }
}

function make(kind: Kind, faces: FaceInfo[], params: Param[], f: Partial<Param>): Param {
  const p = newParam(kind, faces, null, params)
  return { ...p, ...f, faces: f.faces ?? p.faces, step: f.step ?? 0.5 }
}

/** 面の向きを言葉に（上・下・横） */
function side(n: number[]) {
  if (n[2] > 0.9) return 'up'
  if (n[2] < -0.9) return 'down'
  return 'side'
}

export function describeFace(f: FaceInfo): string {
  if (f.fillet_r != null) return `フィレット（角の丸み R${fmt(f.fillet_r, 2)}）`
  if (f.kind_code === 1) return f.convex ? 'ボス・軸（円筒）' : '穴（円筒）'
  if (f.kind_code === 2) return f.convex ? '抜き勾配付きのボス（円錐）' : '抜き勾配付きの穴（円錐）'
  if (f.kind_code === 3) return '球面'
  if (f.kind_code === 4) return 'トーラス面'
  if (f.kind_code === 0) {
    const s = side(f.normal)
    return s === 'up' ? '上向きの平らな面' : s === 'down' ? '下向きの平らな面' : '横向きの平らな面'
  }
  return '曲面'
}

export function suggestionsFor(face: FaceInfo, faces: FaceInfo[], params: Param[]): FaceSuggestions {
  const usedBy = params.filter(p => p.faces.some(x => x.face === face.id) || p.ref_face === face.id)
  const out: FaceSuggestions = { face, what: describeFace(face), usedBy, notes: [], items: [], more: [] }

  // フィレット: 周りに合わせて動くので、単独では寸法にしない
  if (face.fillet_r != null) {
    out.notes.push('角の丸み（フィレット）は、周りの寸法を変えると自動でついて動きます。隣の面をクリックしてください。')
    return out
  }

  // 円筒・円錐・球: 直径
  if ([1, 2, 3].includes(face.kind_code) && face.radius != null) {
    const d = 2 * face.radius
    const cone = face.kind_code === 2
    const title = face.kind_code === 3 ? 'この球面の大きさ（直径）を変える'
      : face.convex ? (cone ? 'このボスの根元の太さを変える' : 'このボスの太さ（直径）を変える')
        : (cone ? 'この穴の入口の大きさを変える' : 'この穴の大きさ（直径）を変える')
    const name = (face.kind_code === 3 ? '球の直径' : face.convex ? (cone ? 'ボスの根元の太さ' : 'ボスの直径') : '穴の直径')
      + `（元 φ${fmt(d, 2)}）`
    out.items.push({
      title,
      detail: `いま φ${fmt(d, 2)} mm${cone ? `（抜き勾配 ${fmt(face.semi_angle ?? 0, 2)}° はそのまま）` : ''}`,
      make: () => make('diameter', faces, params, { name, faces: [{ face: face.id, coef: 1 }], ...range(d) }),
    })
  }

  // 平面: 反対側の面との距離（高さ・厚み・幅）
  if (face.kind_code === 0) {
    const cands = parallelPlanes(faces, face.id)                   // 遠い順
    const anti = cands.filter(c => dot(c.face.normal, face.normal) < 0).sort((a, b) => a.dist - b.dist)
    const s = side(face.normal)
    if (anti.length) {
      const q = anti[0]
      const verb = s === 'side' ? '横に動かして、幅・位置を変える' : s === 'up' ? '上下に動かして、高さ・厚みを変える' : '上下に動かして、厚みを変える'
      const base = s === 'up' ? '下の面' : s === 'down' ? '上の面' : '反対側の面'
      out.items.push({
        title: `この面を${verb}`,
        detail: `${base}（#${q.face.id}）からの距離 いま ${fmt(q.dist, 2)} mm`,
        make: () => make('distance', faces, params, {
          name: `${s === 'side' ? '幅・位置' : '高さ'}（元 ${fmt(q.dist, 2)} mm）`,
          faces: [{ face: face.id, coef: 1 }], ref_face: q.face.id, ...range(q.dist),
        }),
      })
      out.items.push({
        title: '反対側の面と一緒に、両側へ均等に厚みを変える',
        detail: `#${face.id} と #${q.face.id} の間 いま ${fmt(q.dist, 2)} mm（リブ・壁の厚みなど）`,
        make: () => make('distance', faces, params, {
          name: `厚み（元 ${fmt(q.dist, 2)} mm）`,
          faces: [{ face: face.id, coef: 0.5 }, { face: q.face.id, coef: 0.5 }], ref_face: q.face.id, ...range(q.dist),
        }),
      })
      // 基準にする面を選び直す（同じ向き・遠い面も含めて、近い順に数個）
      for (const c of [...cands].sort((a, b) => a.dist - b.dist).filter(c => c.face.id !== q.face.id).slice(0, 4)) {
        out.more.push({
          title: `#${c.face.id} からの距離にする`,
          detail: `いま ${fmt(c.dist, 2)} mm`,
          make: () => make('distance', faces, params, {
            name: `${s === 'side' ? '幅・位置' : '高さ'}（元 ${fmt(c.dist, 2)} mm）`,
            faces: [{ face: face.id, coef: 1 }], ref_face: c.face.id, ...range(c.dist),
          }),
        })
      }
    }
  }

  // どの面でも: 面に垂直に押し出す／引っ込める
  out.items.push({
    title: face.kind_code === 0 ? 'この面だけを押し出す／引っ込める' : 'この面を、面に垂直な向きに押し出す／引っ込める',
    detail: '動かす量で指定（0 が今の位置、+ で外側へ）',
    make: () => make('offset', faces, params, { name: '面の押し出し量', faces: [{ face: face.id, coef: 1 }], min: -2, max: 2 }),
  })
  if (face.kind_code === 10) out.notes.push('自由な形の曲面なので、面に垂直な向きにだけ動かせます。')
  return out
}
