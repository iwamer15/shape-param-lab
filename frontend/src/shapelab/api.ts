// shape-param-lab の API クライアント。埋め込み先では base（例: '/api/shape'）を変えて使う。
import type { CheckResult, DoeSettings, FaceInfo, GenRow, Job, MeshData, ModelInfo, Param, Project, Quality, RangeResult } from './types'

export class ShapeApi {
  constructor(public base = '/api/shape') {}

  private async req<T>(path: string, init?: RequestInit): Promise<T> {
    const r = await fetch(this.base + path, init)
    if (!r.ok) throw new Error(await errText(r))
    return r.json() as Promise<T>
  }
  private post<T>(path: string, body: unknown) {
    return this.req<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  }
  private async packed<T>(path: string, init?: RequestInit): Promise<{ info: T; mesh: MeshData }> {
    const r = await fetch(this.base + path, init)
    if (!r.ok) throw new Error(await errText(r))
    return unpack<T>(await r.arrayBuffer())
  }

  meta() { return this.req<{ kinds: Record<string, string>; doe_methods: Record<string, string>; suffixes: string[]; data_dir: string }>('/meta') }
  models() { return this.req<{ name: string; size: number }[]>('/models') }
  demo(kind: 'step' | 'stl' | 'fillet' = 'step') { return this.post<{ name: string; project: string; data: Project }>(`/models/demo?kind=${kind}`, {}) }
  async upload(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return this.req<{ name: string }>('/models/upload', { method: 'POST', body: fd })
  }
  info(name: string, seg = 30) { return this.req<ModelInfo>(`/models/${encodeURIComponent(name)}/info?seg_angle=${seg}`) }
  baseMesh(name: string, seg = 30) { return this.packed<{ faces: number }>(`/models/${encodeURIComponent(name)}/mesh?seg_angle=${seg}`) }
  check(model: string, params: Param[], seg = 30) { return this.post<CheckResult[]>('/check', { model, params, seg_angle: seg }) }
  morph(model: string, params: Param[], values: Record<string, number>, method: string, seg = 30, signal?: AbortSignal) {
    return this.packed<MorphInfo>('/morph', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal,
      body: JSON.stringify({ model, params, values, method, seg_angle: seg }),
    })
  }
  rangeCheck(model: string, params: Param[], method: string, seg = 30, constraints: string[] = []) {
    return this.post<RangeResult[]>('/range-check', { model, params, method, seg_angle: seg, constraints })
  }
  doe(model: string, params: Param[], d: DoeSettings, seg = 30) {
    return this.post<Record<string, number>[]>('/doe', { model, params, ...d, seg_angle: seg })
  }
  projects() { return this.req<{ name: string }[]>('/projects') }
  project(name: string) { return this.req<Project>(`/projects/${encodeURIComponent(name)}`) }
  saveProject(name: string, project: Project) { return this.post<{ name: string }>('/projects', { name, project }) }
  generate(body: { project: Project; out_name: string; formats: string[]; method: string; overwrite: boolean; samples?: Record<string, number>[] }) {
    return this.post<{ job: string; total: number; out: string; path: string }>('/generate', body)
  }
  job(id: string, since: number) { return this.req<Job & { rows: GenRow[] }>(`/jobs/${id}?since=${since}`) }
  cancel(id: string) { return this.post(`/jobs/${id}/cancel`, {}) }
  outputs() { return this.req<{ name: string; samples: number; path: string }[]>('/outputs') }
  zipUrl(name: string) { return `${this.base}/outputs/${encodeURIComponent(name)}/zip` }
}

export interface MorphInfo {
  method: 'none' | 'offset' | 'boolean' | 'morph'
  offsets: Record<string, number>
  mass: ModelInfo['mass']
  quality: Quality
  full: boolean
  seconds: number
}

async function errText(r: Response) {
  try {
    const j = await r.json()
    return typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j)
  } catch {
    return `${r.status} ${r.statusText}`
  }
}

/** [uint32 JSON長][JSON][uint32 N][uint32 M][f32 N*3][u32 M*3][i32 M] */
export function unpack<T>(buf: ArrayBuffer): { info: T; mesh: MeshData } {
  const dv = new DataView(buf)
  const jl = dv.getUint32(0, true)
  const info = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, jl))) as T
  let o = 4 + jl
  const n = dv.getUint32(o, true), m = dv.getUint32(o + 4, true)
  o += 8
  // 4 バイト境界に揃っていない可能性があるのでコピーして取り出す
  const take = (bytes: number) => { const b = buf.slice(o, o + bytes); o += bytes; return b }
  const positions = new Float32Array(take(n * 12))
  const indices = new Uint32Array(take(m * 12))
  const faceIds = new Int32Array(take(m * 4))
  return { info, mesh: { positions, indices, faceIds } }
}

export type { FaceInfo }
