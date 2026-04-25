import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Router, Archive, CheckCircle, XCircle, Clock, Activity,
  LogIn, LogOut, Plus, Play, UserPlus, RefreshCw, Building2, ArrowRight, CircleDashed,
} from 'lucide-react'
import api, { getCurrentEmpresa, getUser, setCurrentEmpresa } from '../services/api'
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

const EVENTOS = {
  login:                 { icon: LogIn,     color: 'text-sky-400',     label: 'Login',             verbo: 'entrou na plataforma' },
  logout:                { icon: LogOut,    color: 'text-slate-400',   label: 'Logout',            verbo: 'saiu da plataforma' },
  device_criado:         { icon: Plus,      color: 'text-emerald-400', label: 'Dispositivo',       verbo: 'adicionou o dispositivo' },
  device_teste_sucesso:  { icon: CheckCircle,color:'text-emerald-400', label: 'Teste OK',          verbo: 'executou teste com sucesso em' },
  device_teste_falha:    { icon: XCircle,   color: 'text-red-400',     label: 'Teste falhou',      verbo: 'executou teste com falha em' },
  usuario_criado:        { icon: UserPlus,  color: 'text-violet-400',  label: 'Novo usuário',      verbo: 'criou o usuário' },
}

function AtividadeItem({ a, mostrarEmpresa }) {
  const cfg = EVENTOS[a.tipo] || { icon: Activity, color: 'text-slate-400', label: a.tipo, verbo: 'registrou' }
  const Icon = cfg.icon
  return (
    <div className="flex items-start gap-3 px-5 py-3.5">
      <div className={`mt-0.5 ${cfg.color} shrink-0`}>
        <Icon size={16} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-white">
          <span className="font-medium">{a.usuario_nome}</span>
          <span className="text-slate-400"> {cfg.verbo} </span>
          {a.alvo_nome && <span className="font-medium text-slate-200">{a.alvo_nome}</span>}
        </p>
        <div className="flex items-center gap-2 text-xs text-slate-500 mt-0.5 flex-wrap">
          <span>{new Date(a.criado_em).toLocaleString('pt-BR')}</span>
          {mostrarEmpresa && a.empresa_nome && (
            <>
              <span>·</span>
              <span className="flex items-center gap-1 text-slate-400">
                <Building2 size={11} /> {a.empresa_nome}
              </span>
            </>
          )}
          {a.ip && (
            <>
              <span>·</span>
              <span className="font-mono">IP {a.ip}</span>
            </>
          )}
          {a.detalhe && (
            <>
              <span>·</span>
              <span className="truncate max-w-[320px]" title={a.detalhe}>{a.detalhe}</span>
            </>
          )}
        </div>
      </div>
      <span className={`text-[10px] uppercase tracking-wider font-medium ${cfg.color} shrink-0`}>{cfg.label}</span>
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const user = getUser() || {}
  const empresa = getCurrentEmpresa()
  const isMaster = user.role === 'admin'
  const modoGeral = isMaster && !empresa?.id

  const [backups, setBackups] = useState([])
  const [devices, setDevices] = useState([])
  const [atividades, setAtividades] = useState([])
  const [empresasStats, setEmpresasStats] = useState([])
  const [loadingAtv, setLoadingAtv] = useState(false)

  async function carregarAtividades() {
    setLoadingAtv(true)
    try {
      const r = await api.get('/atividades/?limit=30')
      setAtividades(r.data)
    } catch {
      setAtividades([])
    } finally {
      setLoadingAtv(false)
    }
  }

  useEffect(() => {
    api.get('/backups/').then(r => setBackups(r.data)).catch(() => {})
    api.get('/devices/').then(r => setDevices(r.data)).catch(() => {})
    carregarAtividades()
    if (modoGeral) {
      api.get('/empresas/stats').then(r => setEmpresasStats(r.data)).catch(() => {})
    }
  }, [modoGeral])

  const total = backups.length
  const sucessos = backups.filter(b => b.status === 'sucesso').length
  const falhas = backups.filter(b => b.status === 'falha').length
  const recentes = backups.slice(0, 8)

  const totaisEmpresas = useMemo(() => {
    if (!modoGeral) return null
    return empresasStats.reduce((acc, e) => ({
      empresas: acc.empresas + 1,
      ativas: acc.ativas + (e.ativo ? 1 : 0),
    }), { empresas: 0, ativas: 0 })
  }, [empresasStats, modoGeral])

  function entrarEmpresa(e) {
    setCurrentEmpresa({ id: e.id, nome: e.nome })
    navigate('/dashboard')
    window.location.reload()
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-slate-400 text-sm mt-1 flex items-center gap-2 flex-wrap">
            {modoGeral ? (
              <>
                <span className="inline-flex items-center gap-1.5 bg-sky-500/10 text-sky-300 px-2 py-0.5 rounded">
                  <Building2 size={12} /> Visão geral · todas as empresas
                </span>
                {totaisEmpresas && (
                  <span className="text-slate-500">
                    {totaisEmpresas.empresas} empresa(s) · {totaisEmpresas.ativas} ativa(s)
                  </span>
                )}
              </>
            ) : empresa?.id ? (
              <span className="inline-flex items-center gap-1.5 bg-emerald-500/10 text-emerald-300 px-2 py-0.5 rounded">
                <Building2 size={12} /> Empresa: {empresa.nome || `#${empresa.id}`}
              </span>
            ) : (
              <span>Visão geral dos backups</span>
            )}
          </p>
        </div>
        {isMaster && (
          empresa?.id ? (
            <button onClick={() => { setCurrentEmpresa(null); window.location.reload() }}
              className="text-xs text-slate-300 hover:text-sky-300 border border-slate-700 hover:border-sky-500/50 rounded-lg px-3 py-1.5 transition-colors">
              Ver todas as empresas
            </button>
          ) : (
            <button onClick={() => navigate('/empresas')}
              className="text-xs text-slate-300 hover:text-sky-300 border border-slate-700 hover:border-sky-500/50 rounded-lg px-3 py-1.5 transition-colors">
              Selecionar empresa
            </button>
          )
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Router} label="Dispositivos" value={devices.length} color="bg-sky-500/20 text-sky-400" />
        <StatCard icon={Archive} label="Backups Total" value={total} color="bg-violet-500/20 text-violet-400" />
        <StatCard icon={CheckCircle} label="Sucessos" value={sucessos} color="bg-emerald-500/20 text-emerald-400" />
        <StatCard icon={XCircle} label="Falhas" value={falhas} color="bg-red-500/20 text-red-400" />
      </div>

      {modoGeral && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl">
          <div className="p-5 border-b border-slate-700 flex items-center gap-2">
            <Building2 size={18} className="text-slate-400" />
            <h2 className="font-semibold text-white">Resumo por empresa</h2>
          </div>
          {empresasStats.length === 0 ? (
            <p className="text-slate-400 text-sm text-center py-8">Nenhuma empresa cadastrada</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-900/50 text-[10px] uppercase tracking-wider text-slate-400">
                  <tr>
                    <th className="text-left px-5 py-2.5">Empresa</th>
                    <th className="text-center px-3 py-2.5">Devices</th>
                    <th className="text-center px-3 py-2.5">Sucesso</th>
                    <th className="text-center px-3 py-2.5">Falha</th>
                    <th className="text-center px-3 py-2.5">Pendente</th>
                    <th className="text-right px-5 py-2.5"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {empresasStats.map(e => (
                    <tr key={e.id} className="hover:bg-slate-700/30">
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2 min-w-0">
                          <div className={`w-2 h-2 rounded-full shrink-0 ${e.ativo ? 'bg-emerald-400' : 'bg-red-400'}`} />
                          <span className="text-white font-medium truncate">{e.nome}</span>
                        </div>
                      </td>
                      <td className="text-center text-sky-400 font-medium">{e.total_devices ?? 0}</td>
                      <td className="text-center text-emerald-400 font-medium">{e.sucessos ?? 0}</td>
                      <td className="text-center text-red-400 font-medium">{e.falhas ?? 0}</td>
                      <td className="text-center text-slate-400 font-medium">{e.sem_backup ?? 0}</td>
                      <td className="text-right pr-5">
                        <button onClick={() => entrarEmpresa(e)}
                          className="inline-flex items-center gap-1 text-sky-400 hover:text-sky-300 text-xs font-medium">
                          Entrar <ArrowRight size={13} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
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
                <div className="min-w-0">
                  <p className="text-sm font-medium text-white truncate">{b.device?.nome || `Device #${b.device_id}`}</p>
                  <p className="text-xs text-slate-400">{b.device?.ip} · {b.device?.fabricante}</p>
                </div>
                <div className="text-right space-y-1 shrink-0 ml-3">
                  <StatusBadge status={b.status} />
                  <p className="text-xs text-slate-500">
                    {new Date(b.criado_em).toLocaleString('pt-BR')}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-slate-800 border border-slate-700 rounded-xl">
          <div className="p-5 border-b border-slate-700 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity size={18} className="text-slate-400" />
              <h2 className="font-semibold text-white">Atividade Recente</h2>
            </div>
            <button onClick={carregarAtividades} disabled={loadingAtv}
              className="text-slate-400 hover:text-white text-xs flex items-center gap-1 transition-colors">
              <RefreshCw size={13} className={loadingAtv ? 'animate-spin' : ''} /> Atualizar
            </button>
          </div>
          <div className="divide-y divide-slate-700 max-h-[520px] overflow-auto">
            {atividades.length === 0 && !loadingAtv && (
              <p className="text-slate-400 text-sm text-center py-8">Nenhuma atividade registrada ainda</p>
            )}
            {atividades.map(a => <AtividadeItem key={a.id} a={a} mostrarEmpresa={modoGeral} />)}
          </div>
        </div>
      </div>
    </div>
  )
}
