import { useEffect, useState } from 'react'
import { Download, RefreshCw, Filter } from 'lucide-react'
import api from '../services/api'
import StatusBadge from '../components/StatusBadge'

export default function Backups() {
  const [backups, setBackups] = useState([])
  const [devices, setDevices] = useState([])
  const [filtro, setFiltro] = useState('')
  const [loading, setLoading] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const [b, d] = await Promise.all([api.get('/backups/'), api.get('/devices/')])
      setBackups(b.data)
      setDevices(d.data)
    } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  function download(id) {
    window.open(`/api/backups/${id}/download`, '_blank')
  }

  const filtered = filtro
    ? backups.filter(b => b.device_id === Number(filtro))
    : backups

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Backups</h1>
          <p className="text-slate-400 text-sm mt-1">Histórico dos últimos 7 backups por dispositivo</p>
        </div>
        <button onClick={load} disabled={loading}
          className="flex items-center gap-2 text-slate-400 hover:text-white border border-slate-600 px-3 py-2 rounded-lg text-sm transition-colors">
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> Atualizar
        </button>
      </div>

      <div className="flex items-center gap-3">
        <Filter size={16} className="text-slate-400" />
        <select value={filtro} onChange={e => setFiltro(e.target.value)}
          className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-500">
          <option value="">Todos os dispositivos</option>
          {devices.map(d => <option key={d.id} value={d.id}>{d.nome} ({d.ip})</option>)}
        </select>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400 text-left">
              <th className="px-5 py-3 font-medium">Dispositivo</th>
              <th className="px-5 py-3 font-medium">Fabricante</th>
              <th className="px-5 py-3 font-medium">Data/Hora</th>
              <th className="px-5 py-3 font-medium">Status</th>
              <th className="px-5 py-3 font-medium">Erro</th>
              <th className="px-5 py-3 font-medium text-right">Download</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {filtered.length === 0 && (
              <tr><td colSpan={6} className="text-center text-slate-400 py-10">Nenhum backup encontrado</td></tr>
            )}
            {filtered.map(b => (
              <tr key={b.id} className="hover:bg-slate-700/40 transition-colors">
                <td className="px-5 py-3.5">
                  <p className="font-medium text-white">{b.device?.nome || `#${b.device_id}`}</p>
                  <p className="text-xs text-slate-400 font-mono">{b.device?.ip}</p>
                </td>
                <td className="px-5 py-3.5 text-slate-300 capitalize">{b.device?.fabricante}</td>
                <td className="px-5 py-3.5 text-slate-300 text-xs font-mono whitespace-nowrap">
                  {new Date(b.criado_em).toLocaleString('pt-BR')}
                </td>
                <td className="px-5 py-3.5"><StatusBadge status={b.status} /></td>
                <td className="px-5 py-3.5 text-xs text-red-400 max-w-xs truncate">{b.erro || '—'}</td>
                <td className="px-5 py-3.5 text-right">
                  {b.status === 'sucesso' && (
                    <button onClick={() => download(b.id)}
                      className="p-1.5 text-sky-400 hover:bg-sky-500/20 rounded transition-colors" title="Baixar backup">
                      <Download size={15} />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
