import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Pencil, Trash2, X, Loader2, Building2, ArrowRight, ShieldCheck, Router, CheckCircle, XCircle, CircleDashed, PlusCircle, MinusCircle } from 'lucide-react'
import api, { setCurrentEmpresa } from '../services/api'

const BLANK = { nome: '', cnpj: '' }

function UltimaAlteracao({ ua }) {
  if (!ua) {
    return <p className="text-xs text-slate-500 italic">Sem alterações registradas</p>
  }
  const isAdd = ua.tipo === 'device_criado'
  const Icon = isAdd ? PlusCircle : MinusCircle
  const color = isAdd ? 'text-emerald-400' : 'text-red-400'
  const verbo = isAdd ? 'cadastrou' : 'removeu'
  return (
    <div className="flex items-start gap-2 text-xs">
      <Icon size={13} className={`${color} shrink-0 mt-0.5`} />
      <div className="min-w-0">
        <p className="text-slate-300 truncate">
          <span className="font-medium">{ua.usuario_nome}</span>
          <span className="text-slate-400"> {verbo} </span>
          <span className="font-medium">{ua.alvo_nome || '—'}</span>
        </p>
        <p className="text-slate-500 mt-0.5">{new Date(ua.criado_em).toLocaleString('pt-BR')}</p>
      </div>
    </div>
  )
}

export default function Empresas() {
  const [empresas, setEmpresas] = useState([])
  const [modal, setModal] = useState(null)
  const [form, setForm] = useState(BLANK)
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const me = JSON.parse(localStorage.getItem('user') || '{}')
  const isMaster = me.role === 'admin'

  async function load() {
    try {
      const { data } = await api.get('/empresas/stats')
      setEmpresas(data)
    } catch {}
  }

  useEffect(() => { load() }, [])

  function openNew() { setForm(BLANK); setModal('new') }
  function openEdit(e) { setForm({ nome: e.nome, cnpj: e.cnpj || '' }); setModal(e.id) }

  async function save() {
    setLoading(true)
    try {
      const payload = { ...form, cnpj: form.cnpj || null }
      if (modal === 'new') await api.post('/empresas/', payload)
      else await api.put(`/empresas/${modal}`, payload)
      setModal(null)
      load()
    } finally { setLoading(false) }
  }

  async function del(id, nome) {
    if (!confirm(`Remover a empresa "${nome}"?\n\nTODOS os dispositivos, backups e usuários vinculados serão perdidos.`)) return
    await api.delete(`/empresas/${id}`)
    load()
  }

  function entrar(empresa) {
    setCurrentEmpresa({ id: empresa.id, nome: empresa.nome })
    navigate('/dashboard')
  }

  if (!isMaster) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-slate-400">
        <ShieldCheck size={40} className="mb-3 text-slate-600" />
        <p>Apenas admin master pode gerenciar empresas.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Empresas</h1>
          <p className="text-slate-400 text-sm mt-1">{empresas.length} empresa(s) cadastrada(s) — selecione para gerenciar</p>
        </div>
        <button onClick={openNew}
          className="flex items-center gap-2 bg-sky-500 hover:bg-sky-400 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          <Plus size={16} /> Nova Empresa
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {empresas.length === 0 && (
          <div className="col-span-full text-center text-slate-400 py-10 bg-slate-800 border border-slate-700 rounded-xl">
            Nenhuma empresa cadastrada
          </div>
        )}
        {empresas.map(e => (
          <div key={e.id} className="bg-slate-800 border border-slate-700 rounded-xl p-5 flex flex-col gap-4 hover:border-sky-500/60 transition-colors">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <div className="w-11 h-11 rounded-xl bg-sky-500/20 text-sky-400 flex items-center justify-center shrink-0">
                  <Building2 size={22} />
                </div>
                <div className="min-w-0">
                  <p className="font-semibold text-white truncate">{e.nome}</p>
                  <p className="text-xs text-slate-400 truncate">{e.cnpj || 'Sem CNPJ'}</p>
                </div>
              </div>
              <span className={`text-xs px-2 py-0.5 rounded shrink-0 ${e.ativo ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                {e.ativo ? 'Ativa' : 'Inativa'}
              </span>
            </div>

            <div className="grid grid-cols-4 gap-2 border-t border-slate-700 pt-3">
              <div className="flex flex-col items-center">
                <div className="flex items-center gap-1 text-sky-400">
                  <Router size={13} />
                  <span className="text-lg font-semibold">{e.total_devices ?? 0}</span>
                </div>
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Devices</span>
              </div>
              <div className="flex flex-col items-center">
                <div className="flex items-center gap-1 text-emerald-400">
                  <CheckCircle size={13} />
                  <span className="text-lg font-semibold">{e.sucessos ?? 0}</span>
                </div>
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Sucesso</span>
              </div>
              <div className="flex flex-col items-center">
                <div className="flex items-center gap-1 text-red-400">
                  <XCircle size={13} />
                  <span className="text-lg font-semibold">{e.falhas ?? 0}</span>
                </div>
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Falha</span>
              </div>
              <div className="flex flex-col items-center">
                <div className="flex items-center gap-1 text-slate-400">
                  <CircleDashed size={13} />
                  <span className="text-lg font-semibold">{e.sem_backup ?? 0}</span>
                </div>
                <span className="text-[10px] text-slate-500 uppercase tracking-wider">Pendente</span>
              </div>
            </div>

            <div className="border-t border-slate-700 pt-3">
              <p className="text-[10px] text-slate-500 uppercase tracking-wider mb-1.5">Última alteração</p>
              <UltimaAlteracao ua={e.ultima_alteracao} />
            </div>

            <div className="flex items-center justify-between border-t border-slate-700 pt-3">
              <button onClick={() => entrar(e)}
                className="flex items-center gap-1.5 text-sky-400 hover:text-sky-300 text-sm font-medium">
                Gerenciar <ArrowRight size={14} />
              </button>
              <div className="flex items-center gap-1">
                <button onClick={() => openEdit(e)} title="Editar"
                  className="p-1.5 text-sky-400 hover:bg-sky-500/20 rounded transition-colors">
                  <Pencil size={15} />
                </button>
                <button onClick={() => del(e.id, e.nome)} title="Remover"
                  className="p-1.5 text-red-400 hover:bg-red-500/20 rounded transition-colors">
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {modal !== null && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-md">
            <div className="flex items-center justify-between p-5 border-b border-slate-700">
              <h2 className="font-semibold text-white">{modal === 'new' ? 'Nova Empresa' : 'Editar Empresa'}</h2>
              <button onClick={() => setModal(null)} className="text-slate-400 hover:text-white"><X size={18} /></button>
            </div>
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">Nome</label>
                <input value={form.nome} onChange={e => setForm(f => ({ ...f, nome: e.target.value }))}
                  placeholder="Ex.: NEXUS Telecom"
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors" />
              </div>
              <div>
                <label className="block text-sm text-slate-400 mb-1.5">CNPJ <span className="text-slate-500">(opcional)</span></label>
                <input value={form.cnpj} onChange={e => setForm(f => ({ ...f, cnpj: e.target.value }))}
                  placeholder="00.000.000/0000-00"
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors" />
              </div>
            </div>
            <div className="flex gap-3 p-5 border-t border-slate-700">
              <button onClick={() => setModal(null)}
                className="flex-1 bg-slate-700 hover:bg-slate-600 text-white py-2 rounded-lg text-sm transition-colors">
                Cancelar
              </button>
              <button onClick={save} disabled={loading || !form.nome.trim()}
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
