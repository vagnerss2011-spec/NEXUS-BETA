import { NavLink, useNavigate } from 'react-router-dom'
import { LayoutDashboard, Router, Archive, Users, LogOut, Shield } from 'lucide-react'

const links = [
  { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/devices', icon: Router, label: 'Dispositivos' },
  { to: '/backups', icon: Archive, label: 'Backups' },
  { to: '/users', icon: Users, label: 'Usuários' },
]

export default function Sidebar() {
  const navigate = useNavigate()
  const user = JSON.parse(localStorage.getItem('user') || '{}')

  function logout() {
    localStorage.clear()
    navigate('/login')
  }

  return (
    <aside className="w-64 bg-slate-800 border-r border-slate-700 flex flex-col">
      <div className="p-5 border-b border-slate-700">
        <div className="flex items-center gap-2">
          <Shield className="text-sky-400" size={22} />
          <span className="font-bold text-lg tracking-wide text-white">NEXUS BETA</span>
        </div>
        <p className="text-xs text-slate-400 mt-1">Backup Manager</p>
      </div>

      <nav className="flex-1 p-3 space-y-1">
        {links.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to}
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
            <p className="text-xs text-slate-400 capitalize">{user.role || ''}</p>
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
