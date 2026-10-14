import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { ShapeLab } from './shapelab'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ShapeLab apiBase="/api/shape" />
  </StrictMode>,
)
