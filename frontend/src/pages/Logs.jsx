import { useEffect, useState } from 'react'
import { RefreshCw, CheckCircle, XCircle, AlertTriangle, Clock, X, Terminal, ChevronRight, Trash2 } from 'lucide-react'
import api from '../services/api'

function duration(inicio, fim) {
  if (!fim) return '—'
  const ms = new Date(fim) - new Date(inicio)
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function StatusIcon({ log }) {
  if (log.erro_geral) return <AlertTriangle size={16} className="text-red-400 shrink-0" />
  if (!log.fim) return <Clock size={16} className="text-sky-400 shrink-0 animate-pulse" />
  if (log.falhas > 0 && log.sucessos === 0) return <XCircle size={16} className="text-red-400 shrink-0" />
  if (log.falhas > 0) return <AlertTriangle size={16} className="text-amber-400 shrink-0" />
  return <CheckCircle size={16} className="text-emerald-400 shrink-0" />
}

function borderColor(log) {
  if (log.erro_geral || (log.falhas > 0 && log.sucessos === 0)) return 'border-l-2 border-red-500'
  if (log.falhas > 0) return 'border-l-2 border-amber-500'
  return ''
}

export default function Logs() {
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(false)
  const [detalhe, setDetalhe] = useState(null)
  const [backupsLog, setBackupsLog] = useState([])
  const [loadingDetalhe, setLoadingDetalhe] = useState(false)
  const me = JSON.parse(localStorage.getItem('user') || '{}')
  const canDelete = ['admin', 'admin_empresa'].includes(me.role)

  async function load() {
    setLoading(true)
    try {
      const r = await api.get('/logs/scheduler?limit=100')
      setLogs(r.data)
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
    if (!confirm('Excluir TODOS os logs do scheduler?\n\nEsta ação não pode ser desfeita. Os backups permanecem, mas sem vínculo com o log de execução.')) return
    try {
      await api.delete('/logs/scheduler')
      fechar()
      load()
    } catch {
      alert('Falha ao excluir os logs.')
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Logs do Scheduler</h1>
          <p className="text-slate-400 text-sm mt-1">Histórico de execuções do backup automático</p>
        </div>
        <div className="flex items-center gap-2">
          {canDelete && logs.length > 0 && (
            <button onClick={deletarTodos}
              className="flex items-center gap-2 text-red-400 hover:text-red-300 border border-red-500/40 hover:border-red-500/70 px-3 py-2 rounded-lg text-sm transition-colors">
              <Trash2 size={15} /> Excluir todos
            </button>
          )}
          <button onClick={load} disabled={loading}
            className="flex items-center gap-2 text-slate-400 hover:text-white border border-slate-600 px-3 py-2 rounded-lg text-sm transition-colors">
            <RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> Atualizar
          </button>
        </div>
      </div>

      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700 text-slate-400 text-left">
              <th className="px-5 py-3 font-medium">Status</th>
              <th className="px-5 py-3 font-medium">Início</th>
              <th className="px-5 py-3 font-medium">Duração</th>
              <th className="px-5 py-3 font-medium">Total</th>
              <th className="px-5 py-3 font-medium text-emerald-400">Sucesso</th>
              <th className="px-5 py-3 font-medium text-red-400">Falha</th>
              <th className="px-5 py-3 font-medium text-right">Ações</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700">
            {logs.length === 0 && !loading && (
              <tr>
                <td colSpan={7} className="text-center text-slate-400 py-10">
                  Nenhuma execução registrada ainda
                </td>
              </tr>
            )}
            {logs.map(log => (
              <tr
                key={log.id}
                onClick={() => abrirDetalhe(log)}
                className={`hover:bg-slate-700/50 cursor-pointer transition-colors ${borderColor(log)}`}
              >
                <td className="px-5 py-3.5"><StatusIcon log={log} /></td>
                <td className="px-5 py-3.5 text-slate-300 text-xs font-mono whitespace-nowrap">
                  {new Date(log.inicio).toLocaleString('pt-BR')}
                </td>
                <td className="px-5 py-3.5 text-slate-400 text-xs font-mono">
                  {duration(log.inicio, log.fim)}
                </td>
                <td className="px-5 py-3.5 text-slate-300">{log.total ?? '—'}</td>
                <td className="px-5 py-3.5 text-emerald-400 font-medium">{log.sucessos ?? '—'}</td>
                <td className="px-5 py-3.5 text-red-400 font-medium">{log.falhas ?? '—'}</td>
                <td className="px-5 py-3.5">
                  <div className="flex items-center gap-1 justify-end">
                    {canDelete && (
                      <button onClick={(ev) => deletarLog(log, ev)} title="Excluir log"
                        className="p-1.5 text-red-400 hover:bg-red-500/20 rounded transition-colors">
                        <Trash2 size={15} />
                      </button>
                    )}
                    <ChevronRight size={14} className="text-slate-500" />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detalhe && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-3xl max-h-[88vh] flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between p-5 border-b border-slate-700 shrink-0">
              <div className="flex items-center gap-3">
                <StatusIcon log={detalhe} />
                <div>
                  <h2 className="font-semibold text-white">Detalhes da execução</h2>
                  <p className="text-xs text-slate-400">
                    {new Date(detalhe.inicio).toLocaleString('pt-BR')} · duração: {duration(detalhe.inicio, detalhe.fim)}
                    {' · '}{detalhe.total ?? 0} dispositivos · {detalhe.sucessos ?? 0} ok · {detalhe.falhas ?? 0} falhas
                  </p>
                </div>
              </div>
              <button onClick={fechar} className="text-slate-400 hover:text-white">
                <X size={18} />
              </button>
            </div>

            {/* Body */}
            <div className="flex-1 overflow-auto p-5 space-y-4">

              {/* Erro geral do scheduler */}
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

              {/* Backups por dispositivo */}
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
                          : 'border-red-700/50 bg-red-900/10'}`}
                      >
                        <div className="flex items-center gap-2 mb-1">
                          {b.status === 'sucesso'
                            ? <CheckCircle size={14} className="text-emerald-400 shrink-0" />
                            : <XCircle size={14} className="text-red-400 shrink-0" />
                          }
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
              <button onClick={fechar}
                className="bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 rounded-lg text-sm transition-colors">
                Fechar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
