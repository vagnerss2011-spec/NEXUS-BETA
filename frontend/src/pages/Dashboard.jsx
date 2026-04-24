import { useEffect, useState } from 'react'
import { Router, Archive, CheckCircle, XCircle, Clock } from 'lucide-react'
import api from '../services/api'
import StatusBadge from '../components/StatusBadge'

function StatCard({ icon: Icon, label, value, color }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl p-5 flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${color}`}>
        <Icon size={22} />
      </div>
      <div>
        <p className="text-slate-400 text-sm">{label}</p>
        <p className="text-2xl font-bold text-white">{value}</p>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [backups, setBackups] = useState([])
  const [devices, setDevices] = useState([])

  useEffect(() => {
    api.get('/backups/').then(r => setBackups(r.data)).catch(() => {})
    api.get('/devices/').then(r => setDevices(r.data)).catch(() => {})
  }, [])

  const total = backups.length
  const sucessos = backups.filter(b => b.status === 'sucesso').length
  const falhas = backups.filter(b => b.status === 'falha').length
  const recentes = backups.slice(0, 8)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Dashboard</h1>
        <p className="text-slate-400 text-sm mt-1">Visão geral dos backups</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Router} label="Dispositivos" value={devices.length} color="bg-sky-500/20 text-sky-400" />
        <StatCard icon={Archive} label="Backups Total" value={total} color="bg-violet-500/20 text-violet-400" />
        <StatCard icon={CheckCircle} label="Sucessos" value={sucessos} color="bg-emerald-500/20 text-emerald-400" />
        <StatCard icon={XCircle} label="Falhas" value={falhas} color="bg-red-500/20 text-red-400" />
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl">
        <div className="p-5 border-b border-slate-700 flex items-center gap-2">
          <Clock size={18} className="text-slate-400" />
          <h2 className="font-semibold text-white">Backups Recentes</h2>
        </div>
        <div className="divide-y divide-slate-700">
          {recentes.length === 0 && (
            <p className="text-slate-400 text-sm text-center py-8">Nenhum backup encontrado</p>
          )}
          {recentes.map(b => (
            <div key={b.id} className="flex items-center justify-between px-5 py-3.5">
              <div>
                <p className="text-sm font-medium text-white">{b.device?.nome || `Device #${b.device_id}`}</p>
                <p className="text-xs text-slate-400">{b.device?.ip} · {b.device?.fabricante}</p>
              </div>
              <div className="text-right space-y-1">
                <StatusBadge status={b.status} />
                <p className="text-xs text-slate-500">
                  {new Date(b.criado_em).toLocaleString('pt-BR')}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
