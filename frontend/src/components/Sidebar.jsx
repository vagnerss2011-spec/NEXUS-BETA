import { NavLink, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Router, Archive, Users, LogOut, Shield, Settings, ScrollText, Building2, Repeat, X } from 'lucide-react'
import api, { getCurrentEmpresa, setCurrentEmpresa } from '../services/api'

// Em mobile (< md) a sidebar vira drawer overlay controlada por `open`/`onClose`.
// Em desktop (md+) o `open` é ignorado e a sidebar fica sempre visível em flex.
export default function Sidebar({ open = false, onClose = () => {} }) {
  const navigate = useNavigate()
  const user = JSON.parse(localStorage.getItem('user') || '{}')
  const empresa = getCurrentEmpresa()
  const isMaster = user.role === 'admin'
  const canSeeUsers = ['admin', 'admin_empresa'].includes(user.role)
  const canSeeSettings = ['admin', 'admin_empresa'].includes(user.role)
  const canSeeLogs = ['admin', 'admin_empresa'].includes(user.role)

  const links = [
    ...(isMaster ? [{ to: '/empresas', icon: Building2, label: 'Empresas' }] : []),
    { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
    { to: '/devices', icon: Router, label: 'Dispositivos' },
    { to: '/backups', icon: Archive, label: 'Backups' },
    ...(canSeeUsers ? [{ to: '/users', icon: Users, label: 'Usuários' }] : []),
    ...(canSeeLogs ? [{ to: '/logs', icon: ScrollText, label: 'Logs' }] : []),
    ...(canSeeSettings ? [{ to: '/settings', icon: Settings, label: 'Configurações' }] : []),
  ]

  async function logout() {
    try { await api.post('/auth/logout') } catch { /* ignora para não travar o logout */ }
    localStorage.clear()
    navigate('/login')
  }

  function trocarEmpresa() {
    setCurrentEmpresa(null)
    navigate('/empresas')
  }

  return (
    <aside
      className={`
        fixed md:static inset-y-0 left-0 z-40
        w-64 bg-slate-800 border-r border-slate-700 flex flex-col
        transform transition-transform duration-200
        ${open ? 'translate-x-0' : '-translate-x-full'}
        md:translate-x-0
      `}
    >
      <div className="p-5 border-b border-slate-700 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Shield className="text-sky-400" size={22} />
            <span className="font-bold text-lg tracking-wide text-white">NEXUS BETA</span>
          </div>
          <p className="text-xs text-slate-400 mt-1">Backup Manager</p>
        </div>
        {/* Botão de fechar drawer — só aparece em mobile */}
        <button
          onClick={onClose}
          className="md:hidden text-slate-400 hover:text-white"
          aria-label="Fechar menu"
        >
          <X size={20} />
        </button>
      </div>

      {empresa?.id && (
        <div className="p-4 border-b border-slate-700">
          <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Empresa atual</p>
          <div className="flex items-center gap-2">
            <Building2 size={15} className="text-sky-400 shrink-0" />
            <p className="text-sm text-white font-medium truncate flex-1">{empresa.nome || `#${empresa.id}`}</p>
          </div>
          {isMaster && (
            <button onClick={() => { trocarEmpresa(); onClose() }}
              className="mt-2 w-full flex items-center justify-center gap-1.5 text-xs text-slate-400 hover:text-sky-400 border border-slate-700 hover:border-sky-500/50 rounded-md py-1.5 transition-colors">
              <Repeat size={12} /> Trocar empresa
            </button>
          )}
        </div>
      )}

      <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
        {links.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to}
            onClick={onClose}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                isActive
                  ? 'bg-sky-500/20 text-sky-400 font-medium'
                  : 'text-slate-400 hover:bg-slate-700 hover:text-white'
              }`
            }
          >
            <Icon size={18} />
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="p-4 border-t border-slate-700">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-8 h-8 rounded-full bg-sky-500 flex items-center justify-center text-white font-bold text-sm">
            {user.nome?.[0]?.toUpperCase() || 'U'}
          </div>
          <div className="overflow-hidden">
            <p className="text-sm font-medium text-white truncate">{user.nome || 'Usuário'}</p>
            <p className="text-xs text-slate-400 capitalize">{user.role?.replace('_', ' ') || ''}</p>
          </div>
        </div>
        <button onClick={logout}
          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-slate-400 hover:text-red-400 hover:bg-slate-700 rounded-lg transition-colors">
          <LogOut size={16} /> Sair
        </button>
      </div>
    </aside>
  )
}
