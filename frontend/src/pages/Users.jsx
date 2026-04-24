import { useEffect, useState } from 'react'
import { Plus, Pencil, Trash2, X, Loader2, ShieldCheck } from 'lucide-react'
import api from '../services/api'

const ROLES = ['admin', 'operador', 'viewer']
const BLANK = { nome: '', email: '', senha: '', role: 'viewer' }

const roleBadge = {
  admin: 'bg-violet-500/20 text-violet-400 border border-violet-500/30',
  operador: 'bg-sky-500/20 text-sky-400 border border-sky-500/30',
  viewer: 'bg-slate-600/50 text-slate-300 border border-slate-600',
}

export default function Users() {
  const [users, setUsers] = useState([])
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState(BLANK)
  const [loading, setLoading] = useState(false)
  const me = JSON.parse(localStorage.getItem('user') || '{}')

  async function load() {
    try { const { data } = await api.get('/users/'); setUsers(data) }
    catch { }
  }

  useEffect(() => { load() }, [])

  function openNew() { setForm(BLANK); setModal('new') }
  function openEdit(u) { setForm({ ...u, senha: '' }); setModal(u.id) }

  async function save() {
    setLoading(true)
    try {
      const payload = { ...form }
      if (modal !== 'new' && !payload.senha) delete payload.senha
      if (modal === 'new') await api.post('/users/', payload)
      else await api.put(`/users/${modal}`, payload)
      setModal(null)
      load()
    } finally { setLoading(false) }
  }

  async function del(id) {
    if (!confirm('Remover este usuário?')) return
    await api.delete(`/users/${id}`)
    load()
  }

  if (me.role !== 'admin') {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-slate-400">
        <ShieldCheck size={40} className="mb-3 text-slate-600" />
        <p>Apenas administradores podem gerenciar usuários.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Usuários</h1>
          <p className="text-slate-400 text-sm mt-1">{users.length} usuário(s) cadastrado(s)</p>
        </div>
        <button onClick={openNew}
          className="flex items-center gap-2 bg-sky-500 hover:bg-sky-400 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          <Plus size={16} /> Novo Usuário
        </button>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400 text-left">
              <th className="px-5 py-3 font-medium">Usuário</th>
              <th className="px-5 py-3 font-medium">E-mail</th>
              <th className="px-5 py-3 font-medium">Perfil</th>
              <th className="px-5 py-3 font-medium">Status</th>
              <th className="px-5 py-3 font-medium text-right">Ações</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {users.map(u => (
              <tr key={u.id} className="hover:bg-slate-700/40 transition-colors">
                <td className="px-5 py-3.5">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-sky-500/30 flex items-center justify-center text-sky-400 font-bold text-sm">
                      {u.nome[0]?.toUpperCase()}
                    </div>
                    <span className="font-medium text-white">{u.nome}</span>
                  </div>
                </td>
                <td className="px-5 py-3.5 text-slate-300">{u.email}</td>
                <td className="px-5 py-3.5">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium capitalize ${roleBadge[u.role]}`}>
                    {u.role}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  <span className={`text-xs ${u.ativo ? 'text-emerald-400' : 'text-red-400'}`}>
                    {u.ativo ? 'Ativo' : 'Inativo'}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  <div className="flex items-center gap-2 justify-end">
                    <button onClick={() => openEdit(u)}
                      className="p-1.5 text-sky-400 hover:bg-sky-500/20 rounded transition-colors">
                      <Pencil size={15} />
                    </button>
                    {u.id !== me.id && (
                      <button onClick={() => del(u.id)}
                        className="p-1.5 text-red-400 hover:bg-red-500/20 rounded transition-colors">
                        <Trash2 size={15} />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {modal !== null && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-md">
            <div className="flex items-center justify-between p-5 border-b border-slate-700">
              <h2 className="font-semibold text-white">{modal === 'new' ? 'Novo Usuário' : 'Editar Usuário'}</h2>
              <button onClick={() => setModal(null)} className="text-slate-400 hover:text-white"><X size={18} /></button>
            </div>
            <div className="p-5 space-y-4">
              {[
                { label: 'Nome', key: 'nome', placeholder: 'João Silva' },
                { label: 'E-mail', key: 'email', placeholder: 'joao@empresa.com', type: 'email' },
                { label: modal === 'new' ? 'Senha' : 'Nova Senha (deixe em branco para manter)', key: 'senha', placeholder: '••••••••', type: 'password' },
              ].map(({ label, key, placeholder, type = 'text' }) => (
                <div key={key}>
                  <label className="block text-sm text-slate-400 mb-1.5">{label}</label>
                  <input type={type} value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                    placeholder={placeholder}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors" />
                </div>
              ))}
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Perfil</label>
                <select value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500">
                  {ROLES.map(r => <option key={r} value={r} className="capitalize">{r}</option>)}
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
