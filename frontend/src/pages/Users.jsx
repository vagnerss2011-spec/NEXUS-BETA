import { useEffect, useState } from 'react'
import { Plus, Pencil, Trash2, X, Loader2, ShieldCheck, KeyRound, AlertTriangle } from 'lucide-react'
import api, { getCurrentEmpresa } from '../services/api'

const roleBadge = {
  admin: 'bg-violet-500/20 text-violet-400 border border-violet-500/30',
  admin_empresa: 'bg-indigo-500/20 text-indigo-400 border border-indigo-500/30',
  operador: 'bg-sky-500/20 text-sky-400 border border-sky-500/30',
  viewer: 'bg-slate-600/50 text-slate-300 border border-slate-600',
}

const roleLabel = {
  admin: 'admin master',
  admin_empresa: 'admin empresa',
  operador: 'operador',
  viewer: 'viewer',
}

export default function Users() {
  const [users, setUsers] = useState([])
  const [empresas, setEmpresas] = useState([])
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState(null)
  const [loading, setLoading] = useState(false)
  const me = JSON.parse(localStorage.getItem('user') || '{}')
  const empresa = getCurrentEmpresa()
  const isMaster = me.role === 'admin'
  const canManage = ['admin', 'admin_empresa'].includes(me.role)
  const empresaById = Object.fromEntries(empresas.map(e => [e.id, e.nome]))

  // Roles que este usuário pode criar/atribuir
  const assignableRoles = isMaster
    ? ['admin', 'admin_empresa', 'operador', 'viewer']
    : ['admin_empresa', 'operador', 'viewer']

  function blankForm() {
    return {
      nome: '',
      email: '',
      senha: '',
      role: 'viewer',
      // master precisa da empresa atual selecionada; demais usam a própria
      empresa_id: isMaster ? (empresa?.id || null) : me.empresa_id,
    }
  }

  async function load() {
    try {
      const { data } = await api.get('/users/')
      setUsers(data)
    } catch {}
    if (isMaster) {
      try {
        const r = await api.get('/empresas/')
        setEmpresas(r.data)
      } catch {}
    }
  }

  useEffect(() => { load() }, [])

  function openNew() { setForm(blankForm()); setModal('new') }
  function openEdit(u) { setForm({ ...u, senha: '' }); setModal(u.id) }

  async function save() {
    setLoading(true)
    try {
      const payload = { ...form }
      // Se role for admin master, empresa_id deve ser null
      if (payload.role === 'admin') payload.empresa_id = null
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

  if (!canManage) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-slate-400">
        <ShieldCheck size={40} className="mb-3 text-slate-600" />
        <p>Apenas administradores podem gerenciar usuários.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Usuários</h1>
          <p className="text-slate-400 text-sm mt-1">
            {users.length} usuário(s) {isMaster ? 'no sistema (todas as empresas)' : 'cadastrado(s)'}
          </p>
        </div>
        <button onClick={openNew}
          className="flex items-center justify-center gap-2 bg-sky-500 hover:bg-sky-400 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          <Plus size={16} /> Novo Usuário
        </button>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[640px]">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400 text-left">
              <th className="px-5 py-3 font-medium">Usuário</th>
              <th className="px-5 py-3 font-medium">E-mail</th>
              <th className="px-5 py-3 font-medium">Perfil</th>
              {isMaster && <th className="px-5 py-3 font-medium">Empresa</th>}
              <th className="px-5 py-3 font-medium">Status</th>
              <th className="px-5 py-3 font-medium text-right">Ações</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {users.length === 0 && (
              <tr><td colSpan={isMaster ? 6 : 5} className="text-center text-slate-400 py-10">Nenhum usuário encontrado</td></tr>
            )}
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
                <td className="px-5 py-3.5 text-slate-300">
                  <span className="flex items-center gap-2">
                    {u.email}
                    {u.senha_temporaria && (
                      <span title="Aguardando troca de senha no primeiro login"
                        className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">
                        <KeyRound size={10} /> senha temp.
                      </span>
                    )}
                  </span>
                </td>
                <td className="px-5 py-3.5">
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${roleBadge[u.role] || roleBadge.viewer}`}>
                    {roleLabel[u.role] || u.role}
                  </span>
                </td>
                {isMaster && (
                  <td className="px-5 py-3.5 text-slate-300 text-sm">
                    {u.role === 'admin'
                      ? <span className="text-slate-500 italic">global</span>
                      : (empresaById[u.empresa_id] || `#${u.empresa_id ?? '—'}`)}
                  </td>
                )}
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
      </div>

      {modal !== null && form && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-md">
            <div className="flex items-center justify-between p-4 sm:p-5 border-b border-slate-700">
              <h2 className="font-semibold text-white">{modal === 'new' ? 'Novo Usuário' : 'Editar Usuário'}</h2>
              <button onClick={() => setModal(null)} className="text-slate-400 hover:text-white"><X size={18} /></button>
            </div>
            <div className="p-4 sm:p-5 space-y-4">
              {modal === 'new' && (
                <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-200 flex items-start gap-2">
                  <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                  <span>A senha digitada aqui será <strong>temporária</strong>. No primeiro login o usuário será obrigado a definir uma nova.</span>
                </div>
              )}
              {[
                { label: 'Nome', key: 'nome', placeholder: 'João Silva' },
                { label: 'E-mail', key: 'email', placeholder: 'joao@empresa.com', type: 'email' },
                { label: modal === 'new' ? 'Senha' : 'Nova Senha (deixe em branco para manter)', key: 'senha', placeholder: '••••••••', type: 'password' },
              ].map(({ label, key, placeholder, type = 'text' }) => (
                <div key={key}>
                  <label className="block text-sm text-slate-400 mb-1.5">{label}</label>
                  <input type={type} value={form[key] || ''} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                    placeholder={placeholder}
                    className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors" />
                </div>
              ))}
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Perfil</label>
                <select value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500">
                  {assignableRoles.map(r => <option key={r} value={r}>{roleLabel[r]}</option>)}
                </select>
              </div>
              {form.role !== 'admin' && (
                isMaster ? (
                  <div>
                    <label className="block text-sm text-slate-400 mb-1.5">Empresa</label>
                    <select value={form.empresa_id || ''} onChange={e => setForm(f => ({ ...f, empresa_id: e.target.value ? Number(e.target.value) : null }))}
                      className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500">
                      <option value="">— selecione —</option>
                      {empresas.map(e => <option key={e.id} value={e.id}>{e.nome}</option>)}
                    </select>
                  </div>
                ) : (
                  <div className="text-xs text-slate-400 bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2">
                    Vinculado à empresa: <span className="text-white font-medium">(sua empresa)</span>
                  </div>
                )
              )}
            </div>
            <div className="flex gap-3 p-4 sm:p-5 border-t border-slate-700">
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
