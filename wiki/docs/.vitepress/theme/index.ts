// Custom VitePress theme entry: click-to-zoom for Mermaid diagrams and images.
// Mermaid rendering itself is handled at build time by vitepress-plugin-mermaid
// (see .vitepress/config.mts). Diagrams land in the DOM as `.mermaid-diagram`.
import DefaultTheme from 'vitepress/theme'
import type { Theme } from 'vitepress'
import { onMounted, nextTick, watch } from 'vue'
import { useRoute } from 'vitepress'
import mediumZoom from 'medium-zoom'
import './custom.css'
import DiagramZoom from './components/DiagramZoom.vue'

function openOverlay(svgHtml: string) {
  const overlay = document.createElement('div')
  overlay.className = 'diagram-overlay'
  overlay.innerHTML = svgHtml
  const close = () => overlay.remove()
  overlay.addEventListener('click', close)
  document.addEventListener('keydown', function esc(e) {
    if (e.key === 'Escape') {
      close()
      document.removeEventListener('keydown', esc)
    }
  })
  document.body.appendChild(overlay)
}

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('DiagramZoom', DiagramZoom)
  },
  setup() {
    const route = useRoute()
    const run = async () => {
      await nextTick()
      // Image zoom for any <img> in the main content.
      mediumZoom('.vp-doc :not(a) > img', { background: '#0d1117' })
      // Click-to-zoom for Mermaid diagrams rendered by vitepress-plugin-mermaid.
      document.querySelectorAll<HTMLElement>('.mermaid-diagram').forEach((host) => {
        if (host.dataset.zoomBound === 'true') return
        host.dataset.zoomBound = 'true'
        host.style.cursor = 'zoom-in'
        host.addEventListener('click', () => openOverlay(host.innerHTML))
      })
    }
    onMounted(run)
    watch(() => route.path, run)
  },
} satisfies Theme
