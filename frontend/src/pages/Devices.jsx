import { useEffect, useState } from 'react'
import { Plus, Pencil, Trash2, Play, Router, X, Loader2 } from 'lucide-react'
import api from '../services/api'
import StatusBadge from '../components/StatusBadge'

const FABRICANTES = ['mikrotik', 'huawei', 'ubiquiti', 'intelbras', 'outro']
const BLANK = { nome: '', ip: '', porta: 22, fabricante: 'mikrotik', usuario_ssh: '', senha_ssh: '' }

export default function Devices() {
  const [devices, setDevices] = useState([])
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState(BLANK)
  const [loading, setLoading] = useState(false)
  const [runningId, setRunningId] = useState(null)
  const user = JSON.parse(localStorage.getItem('user') || '{}')
  const canEdit = ['admin', 'operador'].includes(user.role)

  async function load() {
    const { data } = await api.get('/devices/')
    setDevices(data)
  }

  useEffect(() => { load() }, [])

  function openNew() { setForm(BLANK); setModal('new') }
  function openEdit(d) { setForm({ ...d, senha_ssh: '' }); setModal(d.id) }

  async function save() {
    setLoading(true)
    try {
      if (modal === 'new') await api.post('/devices/', form)
      else await api.put(`/devices/${modal}`, form)
      setModal(null)
      load()
    } finally { setLoading(false) }
  }

  async function del(id) {
    if (!confirm('Remover este dispositivo?')) return
    await api.delete(`/devices/${id}`)
    load()
  }

  async function runBackup(id) {
    setRunningId(id)
    try { await api.post(`/backups/run/${id}`) }
    finally { setRunningId(null); load() }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Dispositivos</h1>
          <p className="text-slate-400 text-sm mt-1">{devices.length} equipamento(s) cadastrado(s)</p>
        </div>
        {canEdit && (
          <button onClick={openNew}
            className="flex items-center gap-2 bg-sky-500 hover:bg-sky-400 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
            <Plus size={16} /> Novo Dispositivo
          </button>
        )}
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400 text-left">
              <th className="px-5 py-3 font-medium">Nome</th>
              <th className="px-5 py-3 font-medium">IP</th>
              <th className="px-5 py-3 font-medium">Fabricante</th>
              <th className="px-5 py-3 font-medium">Status</th>
              {canEdit && <th className="px-5 py-3 font-medium text-right">Ações</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {devices.length === 0 && (
              <tr><td colSpan={5} className="text-center text-slate-400 py-10">Nenhum dispositivo cadastrado</td></tr>
            )}
            {devices.map(d => (
              <tr key={d.id} className="hover:bg-slate-700/40 transition-colors">
                <td className="px-5 py-3.5 font-medium text-white flex items-center gap-2">
                  <Router size={16} className="text-sky-400" />{d.nome}
                </td>
                <td className="px-5 py-3.5 text-slate-300 font-mono">{d.ip}:{d.porta}</td>
                <td className="px-5 py-3.5">
                  <span className="capitalize text-slate-300">{d.fabricante}</span>
                </td>
                <td className="px-5 py-3.5">
                  <StatusBadge status={d.ativo ? 'sucesso' : 'falha'} />
                </td>
                {canEdit && (
                  <td className="px-5 py-3.5">
                    <div className="flex items-center gap-2 justify-end">
                      <button onClick={() => runBackup(d.id)} disabled={runningId === d.id}
                        title="Executar backup agora"
                        className="p-1.5 text-emerald-400 hover:bg-emerald-500/20 rounded transition-colors">
                        {runningId === d.id ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                      </button>
                      <button onClick={() => openEdit(d)} title="Editar"
                        className="p-1.5 text-sky-400 hover:bg-sky-500/20 rounded transition-colors">
                        <Pencil size={15} />
                      </button>
                      {user.role === 'admin' && (
                        <button onClick={() => del(d.id)} title="Remover"
                          className="p-1.5 text-red-400 hover:bg-red-500/20 rounded transition-colors">
                          <Trash2 size={15} />
                        </button>
                      )}
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal !== null && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-md">
            <div className="flex items-center justify-between p-5 border-b border-slate-700">
              <h2 className="font-semibold text-white">
                {modal === 'new' ? 'Novo Dispositivo' : 'Editar Dispositivo'}
              </h2>
              <button onClick={() => setModal(null)} className="text-slate-400 hover:text-white"><X size={18} /></button>
            </div>
            <div className="p-5 space-y-4">
              {[
                { label: 'Nome', key: 'nome', placeholder: 'Router-Core-SP' },
                { label: 'IP', key: 'ip', placeholder: '192.168.1.1' },
                { label: 'Porta SSH', key: 'porta', placeholder: '22', type: 'number' },
                { label: 'Usuário SSH', key: 'usuario_ssh', placeholder: 'admin' },
                { label: 'Senha SSH', key: 'senha_ssh', placeholder: '••••••••', type: 'password' },
              ].map(({ label, key, placeholder, type = 'text' }) => (
                <div key={key}>
                  <label className="block text-sm text-slate-400 mb-1.5">{label}</label>
                  <input type={type} value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                    placeholder={placeholder}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors" />
                </div>
              ))}
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Fabricante</label>
                <select value={form.fabricante} onChange={e => setForm(f => ({ ...f, fabricante: e.target.value }))}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500">
                  {FABRICANTES.map(f => <option key={f} value={f} className="capitalize">{f}</option>)}
                </select>
              </div>
            </div>
            <div className="flex gap-3 p-5 border-t border-slate-700">
              <button onClick={() => setModal(null)}
                className="flex-1 bg-slate-700 hover:bg-slate-600 text-white py-2 rounded-lg text-sm transition-colors">
                Cancelar
              </button>
              <button onClick={save} disabled={loading}
                className="flex-1 bg-sky-500 hover:bg-sky-400 disabled:opacity-60 text-white py-2 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2">
                {loading && <Loader2 size={14} className="animate-spin" />}
                Salvar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
