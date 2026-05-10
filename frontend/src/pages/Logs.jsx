import { useEffect, useState } from 'react'
import { RefreshCw, CheckCircle, XCircle, AlertTriangle, Clock, X, Terminal, ChevronRight, Trash2, Building2, Upload, ShieldOff, TrendingUp, FileWarning } from 'lucide-react'
import api, { getCurrentEmpresa } from '../services/api'

// Tipos de Atividade que entram na coluna Push.
const PUSH_TIPOS = [
  'ftp_backup_recebido',
  'ftp_backup_falha',
  'ftp_acesso_negado',
  'ftp_volume_alto',
]

// Limites de fetch por fonte. Total renderizado: até 200 linhas
// (manageable na UI, e cobre semana típica de operação).
const LIMIT_SCHEDULER = 100
const LIMIT_PUSH = 200

function duration(inicio, fim) {
  if (!fim) return '—'
  const ms = new Date(fim) - new Date(inicio)
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// Backend grava detalhe como "PROTOCOLO · motivo[ · arquivo]" (ver
// _formatar_detalhe em push_backup.py). Frontend faz o parse pra renderizar.
function parsePushDetalhe(detalhe) {
  if (!detalhe) return { protocolo: '', motivo: '', arquivo: '' }
  const partes = detalhe.split(' · ')
  return {
    protocolo: partes[0] || '',
    motivo: partes[1] || '',
    arquivo: partes[2] || '',
  }
}

// Status visual + cor por tipo de evento push.
function pushStatusBadge(tipo) {
  switch (tipo) {
    case 'ftp_backup_recebido':
      return { Icon: CheckCircle, color: 'text-emerald-400', label: 'recebido', cls: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300' }
    case 'ftp_backup_falha':
      return { Icon: FileWarning, color: 'text-amber-400', label: 'falha', cls: 'bg-amber-500/10 border-amber-500/30 text-amber-300' }
    case 'ftp_acesso_negado':
      return { Icon: ShieldOff, color: 'text-red-400', label: 'negado', cls: 'bg-red-500/10 border-red-500/30 text-red-300' }
    case 'ftp_volume_alto':
      return { Icon: TrendingUp, color: 'text-orange-400', label: 'volume alto', cls: 'bg-orange-500/10 border-orange-500/30 text-orange-300' }
    default:
      return { Icon: AlertTriangle, color: 'text-slate-400', label: tipo, cls: 'bg-slate-500/10 border-slate-500/30 text-slate-300' }
  }
}

// Badge de status para linha de scheduler — equivalente ao StatusIcon antigo
// mas devolve { Icon, color, label } pra render uniforme.
function schedulerStatusBadge(log) {
  if (log.erro_geral) return { Icon: AlertTriangle, color: 'text-red-400', label: 'erro crítico' }
  if (!log.fim) return { Icon: Clock, color: 'text-sky-400 animate-pulse', label: 'rodando' }
  if (log.falhas > 0 && log.sucessos === 0) return { Icon: XCircle, color: 'text-red-400', label: 'todas falharam' }
  if (log.falhas > 0) return { Icon: AlertTriangle, color: 'text-amber-400', label: 'parcial' }
  return { Icon: CheckCircle, color: 'text-emerald-400', label: 'sucesso' }
}

export default function Logs() {
  // Lista mesclada de eventos cronológicos (scheduler + push).
  // Cada item tem source: 'scheduler' | 'push' e um payload original.
  const [eventos, setEventos] = useState([])
  const [loading, setLoading] = useState(false)
  const [detalhe, setDetalhe] = useState(null)  // log de scheduler aberto no modal
  const [backupsLog, setBackupsLog] = useState([])
  const [loadingDetalhe, setLoadingDetalhe] = useState(false)
  const [filtro, setFiltro] = useState('all')   // 'all' | 'scheduler' | 'push'

  const me = JSON.parse(localStorage.getItem('user') || '{}')
  const empresaSelecionada = getCurrentEmpresa()
  const canDelete = ['admin', 'admin_empresa'].includes(me.role)
  // Coluna "Empresa" só aparece para admin master visualizando todas as empresas
  const mostrarEmpresa = me.role === 'admin' && !empresaSelecionada

  async function load() {
    setLoading(true)
    try {
      // Fetch paralelo: scheduler runs + push events.
      const [schedRes, pushRes] = await Promise.all([
        api.get(`/logs/scheduler?limit=${LIMIT_SCHEDULER}`),
        api.get(`/atividades/?tipos=${PUSH_TIPOS.join(',')}&limit=${LIMIT_PUSH}`),
      ])

      const schedItems = (schedRes.data || []).map(log => ({
        source: 'scheduler',
        timestamp: log.criado_em || log.inicio,
        log,
      }))
      const pushItems = (pushRes.data || []).map(atv => ({
        source: 'push',
        timestamp: atv.criado_em,
        atv,
      }))

      const merged = [...schedItems, ...pushItems].sort((a, b) =>
        new Date(b.timestamp) - new Date(a.timestamp)
      )
      setEventos(merged)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function abrirDetalhe(log) {
    setDetalhe(log)
    setBackupsLog([])
    setLoadingDetalhe(true)
    try {
      const r = await api.get(`/logs/scheduler/${log.id}/backups`)
      setBackupsLog(r.data)
    } finally {
      setLoadingDetalhe(false)
    }
  }

  function fechar() {
    setDetalhe(null)
    setBackupsLog([])
  }

  async function deletarLog(log, ev) {
    ev?.stopPropagation()
    if (!confirm(`Excluir este log de ${new Date(log.inicio).toLocaleString('pt-BR')}?`)) return
    try {
      await api.delete(`/logs/scheduler/${log.id}`)
      if (detalhe?.id === log.id) fechar()
      load()
    } catch {
      alert('Falha ao excluir o log.')
    }
  }

  async function deletarTodos() {
    if (!confirm('Excluir TODOS os logs do scheduler?\n\nEsta ação não pode ser desfeita. Os backups e os eventos de push permanecem.')) return
    try {
      await api.delete('/logs/scheduler')
      fechar()
      load()
    } catch {
      alert('Falha ao excluir os logs.')
    }
  }

  async function deletarPush(atv, ev) {
    ev?.stopPropagation()
    const quando = new Date(atv.criado_em).toLocaleString('pt-BR')
    const alvo = atv.alvo_nome ? ` de ${atv.alvo_nome}` : ''
    if (!confirm(`Excluir este evento de push${alvo} (${quando})?`)) return
    try {
      await api.delete(`/atividades/${atv.id}`)
      load()
    } catch {
      alert('Falha ao excluir o evento.')
    }
  }

  async function deletarTodosPush() {
    if (!confirm('Excluir TODOS os eventos de push (FTP/SFTP/TFTP)?\n\nInclui sucessos, falhas, acessos negados e alertas de volume alto. Esta ação não pode ser desfeita.\nOs backups recebidos permanecem — só os logs são removidos.')) return
    try {
      const r = await api.delete(`/atividades/?tipos=${PUSH_TIPOS.join(',')}`)
      load()
      if (r.data?.removidos > 0) {
        // Feedback discreto (sem alert) — load() já mostra a tabela vazia/atualizada
      }
    } catch {
      alert('Falha ao excluir os eventos de push.')
    }
  }

  // Aplica o filtro de aba (all / scheduler / push) na lista mesclada.
  const eventosFiltrados = eventos.filter(e => filtro === 'all' || e.source === filtro)

  // Contadores por fonte pra mostrar nas abas.
  const totalScheduler = eventos.filter(e => e.source === 'scheduler').length
  const totalPush = eventos.filter(e => e.source === 'push').length

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Logs do Sistema</h1>
          <p className="text-slate-400 text-sm mt-1">Histórico de execuções (scheduler) + eventos de push (FTP/SFTP/TFTP)</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {canDelete && totalScheduler > 0 && (
            <button onClick={deletarTodos}
              className="flex items-center justify-center gap-2 text-red-400 hover:text-red-300 border border-red-500/40 hover:border-red-500/70 px-3 py-2 rounded-lg text-sm transition-colors flex-1 sm:flex-none">
              <Trash2 size={15} /> Excluir logs scheduler
            </button>
          )}
          {canDelete && totalPush > 0 && (
            <button onClick={deletarTodosPush}
              className="flex items-center justify-center gap-2 text-red-400 hover:text-red-300 border border-red-500/40 hover:border-red-500/70 px-3 py-2 rounded-lg text-sm transition-colors flex-1 sm:flex-none">
              <Trash2 size={15} /> Excluir logs push
            </button>
          )}
          <button onClick={load} disabled={loading}
            className="flex items-center justify-center gap-2 text-slate-400 hover:text-white border border-slate-600 px-3 py-2 rounded-lg text-sm transition-colors flex-1 sm:flex-none">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> Atualizar
          </button>
        </div>
      </div>

      {/* Filtros (chips) — escolha a fonte sem perder o ordenamento global */}
      <div className="flex items-center gap-2 flex-wrap text-sm">
        <button onClick={() => setFiltro('all')}
          className={`px-3 py-1.5 rounded-lg border transition-colors ${
            filtro === 'all'
              ? 'bg-sky-500/20 border-sky-500/50 text-sky-300'
              : 'border-slate-600 text-slate-400 hover:text-white'
          }`}>
          Tudo <span className="text-xs opacity-70 ml-1">({eventos.length})</span>
        </button>
        <button onClick={() => setFiltro('scheduler')}
          className={`px-3 py-1.5 rounded-lg border transition-colors flex items-center gap-1.5 ${
            filtro === 'scheduler'
              ? 'bg-sky-500/20 border-sky-500/50 text-sky-300'
              : 'border-slate-600 text-slate-400 hover:text-white'
          }`}>
          <Clock size={13} /> Scheduler <span className="text-xs opacity-70 ml-1">({totalScheduler})</span>
        </button>
        <button onClick={() => setFiltro('push')}
          className={`px-3 py-1.5 rounded-lg border transition-colors flex items-center gap-1.5 ${
            filtro === 'push'
              ? 'bg-sky-500/20 border-sky-500/50 text-sky-300'
              : 'border-slate-600 text-slate-400 hover:text-white'
          }`}>
          <Upload size={13} /> Push <span className="text-xs opacity-70 ml-1">({totalPush})</span>
        </button>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[820px]">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400 text-left">
              <th className="px-4 py-3 font-medium">Tipo</th>
              <th className="px-4 py-3 font-medium">Quando</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Origem / Device</th>
              <th className="px-4 py-3 font-medium">Detalhe</th>
              {mostrarEmpresa && <th className="px-4 py-3 font-medium">Empresa</th>}
              <th className="px-4 py-3 font-medium text-right">Ações</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {eventosFiltrados.length === 0 && !loading && (
              <tr>
                <td colSpan={mostrarEmpresa ? 7 : 6} className="text-center text-slate-400 py-10">
                  {filtro === 'all'
                    ? 'Nenhum evento registrado ainda'
                    : filtro === 'scheduler'
                      ? 'Nenhuma execução de scheduler registrada'
                      : 'Nenhum evento de push registrado'}
                </td>
              </tr>
            )}
            {eventosFiltrados.map((e, i) => e.source === 'scheduler'
              ? <SchedulerRow key={`s-${e.log.id}`} log={e.log} onOpen={abrirDetalhe} canDelete={canDelete} onDelete={deletarLog} mostrarEmpresa={mostrarEmpresa} />
              : <PushRow key={`p-${e.atv.id}`} atv={e.atv} canDelete={canDelete} onDelete={deletarPush} mostrarEmpresa={mostrarEmpresa} />
            )}
          </tbody>
        </table>
        </div>
      </div>

      {detalhe && (
        <SchedulerDetalheModal
          detalhe={detalhe}
          backupsLog={backupsLog}
          loadingDetalhe={loadingDetalhe}
          onClose={fechar}
        />
      )}
    </div>
  )
}

// ─────────────────── Linha de log de Scheduler ───────────────────
function SchedulerRow({ log, onOpen, canDelete, onDelete, mostrarEmpresa }) {
  const { Icon, color, label } = schedulerStatusBadge(log)
  const empresasResumo = (log.empresas || []).length
  const borderClass = log.erro_geral || (log.falhas > 0 && log.sucessos === 0)
    ? 'border-l-2 border-red-500'
    : log.falhas > 0 ? 'border-l-2 border-amber-500' : ''

  return (
    <tr onClick={() => onOpen(log)}
        className={`hover:bg-slate-700/50 cursor-pointer transition-colors ${borderClass}`}>
      <td className="px-4 py-3">
        <span className="inline-flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded font-medium bg-sky-500/10 border border-sky-500/30 text-sky-300">
          <Clock size={11} /> Scheduler
        </span>
      </td>
      <td className="px-4 py-3 text-slate-300 text-xs font-mono whitespace-nowrap">
        {new Date(log.inicio).toLocaleString('pt-BR')}
      </td>
      <td className="px-4 py-3">
        <span className={`inline-flex items-center gap-1.5 ${color}`}>
          <Icon size={14} /> <span className="text-xs">{label}</span>
        </span>
      </td>
      <td className="px-4 py-3 text-slate-300">
        <span className="text-xs">{log.total ?? 0} devices</span>
        <span className="text-xs text-emerald-400 ml-2">{log.sucessos ?? 0} ok</span>
        {log.falhas > 0 && <span className="text-xs text-red-400 ml-2">{log.falhas} falha{log.falhas === 1 ? '' : 's'}</span>}
      </td>
      <td className="px-4 py-3 text-slate-400 text-xs">
        SSH/Telnet · duração: {duration(log.inicio, log.fim)}
      </td>
      {mostrarEmpresa && (
        <td className="px-4 py-3">
          {empresasResumo > 0 ? (
            <span className="text-xs text-slate-400">{empresasResumo} empresa{empresasResumo === 1 ? '' : 's'}</span>
          ) : (
            <span className="text-xs text-slate-500 italic">—</span>
          )}
        </td>
      )}
      <td className="px-4 py-3">
        <div className="flex items-center gap-1 justify-end">
          {canDelete && (
            <button onClick={(ev) => onDelete(log, ev)} title="Excluir log"
                    className="p-1.5 text-red-400 hover:bg-red-500/20 rounded transition-colors">
              <Trash2 size={15} />
            </button>
          )}
          <ChevronRight size={14} className="text-slate-500" />
        </div>
      </td>
    </tr>
  )
}

// ─────────────────── Linha de evento Push ───────────────────
function PushRow({ atv, canDelete, onDelete, mostrarEmpresa }) {
  const { protocolo, motivo, arquivo } = parsePushDetalhe(atv.detalhe)
  const { Icon, color, label, cls } = pushStatusBadge(atv.tipo)

  // Borda esquerda colorida nos casos não-sucesso pra dar destaque na varredura.
  const borderClass = atv.tipo === 'ftp_backup_falha' ? 'border-l-2 border-amber-500'
                    : atv.tipo === 'ftp_acesso_negado' ? 'border-l-2 border-red-500'
                    : atv.tipo === 'ftp_volume_alto' ? 'border-l-2 border-orange-500'
                    : ''

  return (
    <tr className={`hover:bg-slate-700/30 transition-colors ${borderClass}`}>
      <td className="px-4 py-3">
        <span className="inline-flex items-center gap-1.5 text-[11px] px-2 py-0.5 rounded font-medium bg-violet-500/10 border border-violet-500/30 text-violet-300">
          <Upload size={11} /> Push
          {protocolo && <span className="text-violet-400/70 font-mono ml-0.5">·{protocolo}</span>}
        </span>
      </td>
      <td className="px-4 py-3 text-slate-300 text-xs font-mono whitespace-nowrap">
        {new Date(atv.criado_em).toLocaleString('pt-BR')}
      </td>
      <td className="px-4 py-3">
        <span className={`inline-flex items-center gap-1.5 ${color}`}>
          <Icon size={14} /> <span className="text-xs">{label}</span>
        </span>
      </td>
      <td className="px-4 py-3 text-slate-300">
        <div className="flex flex-col">
          <span className="text-xs font-medium">{atv.alvo_nome || <span className="italic text-slate-500">(auth)</span>}</span>
          {atv.ip && <span className="text-[11px] text-slate-500 font-mono">{atv.ip}</span>}
        </div>
      </td>
      <td className="px-4 py-3 text-slate-400 text-xs">
        <div className="flex flex-col">
          <span>{motivo}</span>
          {arquivo && <span className="text-[11px] text-slate-500 font-mono mt-0.5 truncate max-w-[280px]">{arquivo}</span>}
        </div>
      </td>
      {mostrarEmpresa && (
        <td className="px-4 py-3">
          {atv.empresa_nome ? (
            <span className="inline-flex items-center gap-1 text-xs text-slate-400">
              <Building2 size={11} /> <span className="truncate max-w-[140px]">{atv.empresa_nome}</span>
            </span>
          ) : (
            <span className="text-xs text-slate-500 italic">—</span>
          )}
        </td>
      )}
      <td className="px-4 py-3">
        <div className="flex items-center gap-1 justify-end">
          <span className={`text-[10px] px-2 py-0.5 rounded border ${cls}`}>{atv.tipo}</span>
          {canDelete && (
            <button onClick={(ev) => onDelete(atv, ev)} title="Excluir este evento"
                    className="p-1.5 text-red-400 hover:bg-red-500/20 rounded transition-colors">
              <Trash2 size={15} />
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}

// ─────────────────── Modal de detalhe (Scheduler) ───────────────────
function SchedulerDetalheModal({ detalhe, backupsLog, loadingDetalhe, onClose }) {
  const { Icon, color } = schedulerStatusBadge(detalhe)
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-3xl max-h-[88vh] flex flex-col">
        <div className="flex items-center justify-between p-4 sm:p-5 border-b border-slate-700 shrink-0">
          <div className="flex items-center gap-3">
            <Icon size={18} className={color} />
            <div>
              <h2 className="font-semibold text-white">Detalhes da execução do scheduler</h2>
              <p className="text-xs text-slate-400">
                {new Date(detalhe.inicio).toLocaleString('pt-BR')} · duração: {duration(detalhe.inicio, detalhe.fim)}
                {' · '}{detalhe.total ?? 0} dispositivos · {detalhe.sucessos ?? 0} ok · {detalhe.falhas ?? 0} falhas
              </p>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-auto p-4 sm:p-5 space-y-4">
          {detalhe.erro_geral && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Terminal size={14} className="text-red-400" />
                <span className="text-sm font-medium text-red-400">Erro crítico do scheduler</span>
              </div>
              <pre className="bg-slate-900 rounded-lg p-4 text-xs text-red-300 whitespace-pre-wrap break-all font-mono leading-relaxed">
                {detalhe.erro_geral}
              </pre>
            </div>
          )}

          {loadingDetalhe ? (
            <p className="text-slate-400 text-sm text-center py-4">Carregando...</p>
          ) : backupsLog.length === 0 ? (
            <p className="text-slate-500 text-sm text-center py-4">Nenhum backup vinculado a esta execução</p>
          ) : (
            <div>
              <p className="text-sm font-medium text-slate-300 mb-2">Resultado por dispositivo</p>
              <div className="space-y-2">
                {backupsLog.map(b => (
                  <div key={b.id}
                    className={`rounded-lg border p-4 ${b.status === 'sucesso'
                      ? 'border-emerald-700/50 bg-emerald-900/10'
                      : 'border-red-700/50 bg-red-900/10'}`}>
                    <div className="flex items-center gap-2 mb-1">
                      {b.status === 'sucesso'
                        ? <CheckCircle size={14} className="text-emerald-400 shrink-0" />
                        : <XCircle size={14} className="text-red-400 shrink-0" />}
                      <span className="font-medium text-white text-sm">
                        {b.device?.nome ?? `Device #${b.device_id}`}
                      </span>
                      <span className="text-xs text-slate-400 font-mono">{b.device?.ip}</span>
                      <span className="text-xs text-slate-500 capitalize">{b.device?.fabricante}</span>
                    </div>
                    {b.status === 'falha' && b.erro && (
                      <pre className="mt-2 bg-slate-900 rounded p-3 text-xs text-red-300 whitespace-pre-wrap break-all font-mono leading-relaxed max-h-40 overflow-auto">
                        {b.erro}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-slate-700 shrink-0">
          <button onClick={onClose}
            className="bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 rounded-lg text-sm transition-colors">
            Fechar
          </button>
        </div>
      </div>
    </div>
  )
}
