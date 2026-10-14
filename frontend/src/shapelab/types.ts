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

export interface DoeSettings { method: string; n: number; seed: number; levels: number }

export interface Project {
  version: number
  model: string
  params: Param[]
  doe: DoeSettings
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
  axis_dir: number[] | null
  convex: boolean | null
  label: string
}

export interface ModelInfo {
  name: string
  faces: FaceInfo[]
  mass: { volume: number; area: number; bbox_min: number[]; bbox_max: number[]; size: number[] }
  deflection: number
}

export interface MeshData {
  positions: Float32Array
  indices: Uint32Array
  faceIds: Int32Array
}

export interface CheckResult { id: string; base: number | null; sign: number; error: string | null }

export interface RangeResult { label: string; ok: boolean; method?: string; volume?: number; error?: string; seconds?: number }

export interface GenRow { sample: string; status: string; method: string; error: string; volume?: number; [k: string]: unknown }

export interface Job {
  id: string; out: string; total: number; done: number; ok: number; failed: number
  status: 'running' | 'done' | 'cancelled' | 'error'; error: string | null
}
