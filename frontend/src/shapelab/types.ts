export type Kind = 'distance' | 'diameter' | 'offset'

export interface FaceRef { face: number; coef: number }

export interface Param {
  id: string
  key: string          // CSV の列名（英数字）
  name: string         // 表示名
  kind: Kind
  faces: FaceRef[]
  ref_face?: number | null
  min: number
  max: number
  step?: number
  enabled?: boolean
}

export interface DoeSettings { method: string; n: number; seed: number; levels: number; constraints?: string[] }

export interface Project {
  version: number
  model: string
  params: Param[]
  doe: DoeSettings
  seg_angle?: number       // STL / INP の面の分割角度（面番号が変わるので定義と一緒に保存）
}

export interface ModelDesc {
  type: 'cad' | 'mesh'
  source?: 'stl' | 'inp'
  label: string
  faces: number
  nodes?: number
  tris?: number
  elements?: number
  seg_angle?: number
  planes?: number
  cylinders?: number
  cones?: number
  spheres?: number
  tori?: number
  others?: number
  seg_tol?: number
  warnings: string[]
}

export interface FaceInfo {
  id: number
  kind: string
  kind_code: number
  area: number
  center: number[]
  point: number[]
  normal: number[]
  radius: number | null
  axis_loc?: number[] | null
  axis_dir: number[] | null
  convex: boolean | null
  semi_angle?: number | null
  fillet_r?: number | null
  label: string
}

export interface ModelInfo {
  name: string
  faces: FaceInfo[]
  mass: { volume: number; area: number; bbox_min: number[]; bbox_max: number[]; size: number[] }
  model: ModelDesc
  formats: string[]
  methods: string[]
}

export interface Quality { flipped_tris?: number; inverted_elements?: number; min_volume_ratio?: number | null }

export interface MeshData {
  positions: Float32Array
  indices: Uint32Array
  faceIds: Int32Array
}

export interface CheckResult { id: string; base: number | null; sign: number; error: string | null }

export interface RangeResult { label: string; ok: boolean; skipped?: boolean; method?: string; volume?: number; error?: string; seconds?: number; quality?: Quality }

export interface GenRow { sample: string; status: string; method: string; error: string; volume?: number; [k: string]: unknown }

export interface Job {
  id: string; out: string; total: number; done: number; ok: number; failed: number
  status: 'running' | 'done' | 'cancelled' | 'error'; error: string | null
}
