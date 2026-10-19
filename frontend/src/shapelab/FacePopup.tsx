// 3D の面をクリックしたときに、その場に出る「どう変えるか」の候補
import { useState } from 'react'
import type { FaceSuggestions } from './suggest'
import type { Param } from './types'

interface Props {
  s: FaceSuggestions
  at: { x: number; y: number }
  box: { w: number; h: number }
  onChoose: (p: Param) => void
  onEdit: (id: string) => void
  onClose: () => void
}

export function FacePopup({ s, at, box, onChoose, onEdit, onClose }: Props) {
  const [more, setMore] = useState(false)
  const W = 320
  // 画面からはみ出さない位置に出す
  const left = Math.min(Math.max(8, at.x + 14), Math.max(8, box.w - W - 8))
  const top = Math.min(Math.max(8, at.y - 20), Math.max(8, box.h - 320))
  return (
    <div className="sl-pop" style={{ left, top, width: W }} onPointerDown={e => e.stopPropagation()}>
      <div className="sl-pop-head">
        <div>
          <b>{s.what}</b>
          <span className="sl-sub">　面 #{s.face.id}</span>
        </div>
        <button className="sl-icon" onClick={onClose} title="閉じる">✕</button>
      </div>
      {s.usedBy.length > 0 && (
        <div className="sl-pop-used">
          この面は {s.usedBy.map((p, i) => (
            <span key={p.id}>{i > 0 && '・'}<button className="sl-link" onClick={() => onEdit(p.id)}>「{p.name || p.key}」</button></span>
          ))} で使っています
        </div>
      )}
      {s.notes.map((n, i) => <p key={i} className="sl-hint">{n}</p>)}
      {s.items.length > 0 && <div className="sl-sub" style={{ margin: '4px 0' }}>どう変えますか？</div>}
      <div className="sl-pop-items">
        {s.items.map((it, i) => (
          <button key={i} className={`sl-pop-item ${i === 0 ? 'first' : ''}`} onClick={() => onChoose(it.make())}>
            <span className="sl-pop-title">{it.title}</span>
            <span className="sl-pop-detail">{it.detail}</span>
          </button>
        ))}
      </div>
      {s.more.length > 0 && (
        <div className="sl-pop-more">
          <button className="sl-link" onClick={() => setMore(!more)}>{more ? '▾' : '▸'} 測る基準の面を選び直す</button>
          {more && s.more.map((it, i) => (
            <button key={i} className="sl-pop-item small" onClick={() => onChoose(it.make())}>
              <span className="sl-pop-title">{it.title}</span>
              <span className="sl-pop-detail">{it.detail}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
