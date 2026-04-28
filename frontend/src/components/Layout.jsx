import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Menu, Shield } from 'lucide-react'
import Sidebar from './Sidebar'

// Em desktop (md+) Sidebar é parte do flex layout; em mobile (< md) ela vira
// drawer overlay controlada por sidebarOpen — botão hambúrguer abre, backdrop
// e clique em NavLink fecham. md:hidden / hidden md:* segregam o que aparece
// onde sem JS extra.
export default function Layout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const closeSidebar = () => setSidebarOpen(false)

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Backdrop só em mobile quando drawer aberto */}
      {sidebarOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/50 z-30"
          onClick={closeSidebar}
          aria-hidden="true"
        />
      )}

      <Sidebar open={sidebarOpen} onClose={closeSidebar} />

      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar — só em mobile. Em md+ a sidebar já tem o branding. */}
        <header className="md:hidden bg-slate-800 border-b border-slate-700 px-3 py-2.5 flex items-center gap-3 shrink-0">
          <button
            onClick={() => setSidebarOpen(true)}
            className="text-slate-300 hover:text-white p-1"
            aria-label="Abrir menu"
          >
            <Menu size={22} />
          </button>
          <div className="flex items-center gap-2">
            <Shield className="text-sky-400" size={18} />
            <span className="font-bold text-sm tracking-wide text-white">NEXUS BETA</span>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto bg-slate-900 p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
