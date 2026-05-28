import { useEffect, useRef, useState } from 'react'
import {
  RefreshCw, GitBranch, Copy, Check, ExternalLink, Info, Loader2,
  Sparkles, ShieldCheck, FlaskConical, AlertTriangle, ChevronDown, ChevronUp,
  Rocket, X, CheckCircle, XCircle,
} from 'lucide-react'
import api, { getUser } from '../services/api'

const ESTADOS_ATIVOS = new Set(['queued', 'running'])
const POLL_INTERVAL_MS = 3000

// Card "Atualização do sistema" no Settings (v2.3.0+).
//
// Mostra:
// - Versão corrente da instância + dias em produção (data do release atual)
// - Canal atual (LTS/Edge) + toggle pra trocar (só admin master)
// - Última LTS e última Edge (com data e link pro release no GitHub)
// - Se há atualização no canal: changelog completo + comando SSH pra copiar
//
// A política de canais é nativa do GitHub Releases:
// - "Latest" (não Pre-release) = LTS
// - "Pre-release" = Edge
// O admin promove uma versão a LTS marcando-a como Latest no GitHub.
//
// Update propriamente dito ainda é manual via SSH na Fase 1 — Fase 2 vai
// adicionar botão "Atualizar agora" via systemd path unit no host.

function formatarData(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' })
  } catch {
    return iso
  }
}

function UpdateStatusPanel({ status }) {
  const cfg = {
    queued:  { Icon: Loader2,    cls: 'border-sky-500/40 bg-sky-500/10 text-sky-200',         label: 'Aguardando o helper do host pegar o pedido…', spin: true  },
    running: { Icon: Loader2,    cls: 'border-sky-500/40 bg-sky-500/10 text-sky-200',         label: 'Atualização em andamento (git checkout + docker build)…', spin: true  },
    success: { Icon: CheckCircle,cls: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200', label: 'Atualização concluída com sucesso.', spin: false },
    failed:  { Icon: XCircle,    cls: 'border-red-500/40 bg-red-500/10 text-red-200',         label: 'Atualização falhou.', spin: false },
  }[status.state]
  if (!cfg) return null
  return (
    <div className={`border rounded-lg p-3 ${cfg.cls}`}>
      <div className="flex items-start gap-2">
        <cfg.Icon size={16} className={cfg.spin ? 'animate-spin mt-0.5 shrink-0' : 'mt-0.5 shrink-0'} />
        <div className="text-sm flex-1 min-w-0">
          <div className="font-medium">{cfg.label}</div>
          {(status.versao_de || status.versao_para) && (
            <div className="text-xs opacity-80 mt-0.5">
              {status.versao_de && <span>de <code className="font-mono">v{status.versao_de}</code></span>}
              {status.versao_para && <span> → <code className="font-mono">{status.versao_para}</code></span>}
              {status.usuario_nome && <span className="ml-2 opacity-70">por {status.usuario_nome}</span>}
            </div>
          )}
          {status.mensagem && (
            <pre className="mt-2 text-[11px] bg-slate-900 rounded p-2 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono">
              {status.mensagem}
            </pre>
          )}
        </div>
      </div>
    </div>
  )
}

function ChannelBadge({ channel, size = 'sm' }) {
  const cfg = channel === 'edge'
    ? { Icon: FlaskConical, label: 'Edge', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/30' }
    : { Icon: ShieldCheck, label: 'LTS',  cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' }
  const px = size === 'xs' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-0.5 text-xs'
  return (
    <span className={`inline-flex items-center gap-1 rounded border font-medium ${px} ${cfg.cls}`}>
      <cfg.Icon size={size === 'xs' ? 10 : 12} /> {cfg.label}
    </span>
  )
}

function ReleaseLine({ label, info }) {
  if (!info) return (
    <div className="flex items-center justify-between text-sm py-1.5">
      <span className="text-slate-400">{label}</span>
      <span className="text-slate-600 text-xs">— sem release</span>
    </div>
  )
  return (
    <div className="flex items-center justify-between text-sm py-1.5">
      <span className="text-slate-400">{label}</span>
      <span className="flex items-center gap-2">
        <span className="text-white font-mono">{info.tag}</span>
        <span className="text-xs text-slate-500">{formatarData(info.published_at)}</span>
        {info.url && (
          <a href={info.url} target="_blank" rel="noopener noreferrer"
            className="text-slate-500 hover:text-sky-400" title="Abrir release no GitHub">
            <ExternalLink size={12} />
          </a>
        )}
      </span>
    </div>
  )
}

export default function UpdateCard() {
  const me = getUser()
  const isMaster = me?.role === 'admin'

  const [info, setInfo] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [savingChannel, setSavingChannel] = useState(false)
  const [changelogOpen, setChangelogOpen] = useState(false)
  const [copiado, setCopiado] = useState('')
  const [erro, setErro] = useState('')

  // Auto-update via painel (Fase 2 — v2.4.0)
  const [updateStatus, setUpdateStatus] = useState(null)   // { state, versao_de, ... }
  const [showConfirm, setShowConfirm] = useState(false)
  const [confirmText, setConfirmText] = useState('')
  const [triggering, setTriggering] = useState(false)
  const [triggerErro, setTriggerErro] = useState('')
  const pollRef = useRef(null)

  async function carregar({ refresh = false } = {}) {
    if (refresh) setRefreshing(true)
    else setLoading(true)
    setErro('')
    try {
      const { data } = await api.get('/version/check', refresh ? { params: { refresh: true } } : {})
      setInfo(data)
    } catch (e) {
      setErro(e.response?.data?.detail || e.message || 'Falha ao consultar versão')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => { carregar() }, [])

  async function trocarCanal(novoCanal) {
    if (!isMaster) return
    if (novoCanal === info?.channel) return
    setSavingChannel(true)
    try {
      await api.patch('/version/channel', { channel: novoCanal })
      // Recarrega forçando refresh pra usar canal novo já
      await carregar({ refresh: true })
    } catch (e) {
      setErro(e.response?.data?.detail || e.message || 'Falha ao trocar canal')
    } finally {
      setSavingChannel(false)
    }
  }

  function copiar(texto, label) {
    navigator.clipboard.writeText(texto)
    setCopiado(label)
    setTimeout(() => setCopiado(''), 1500)
  }

  // ───── Auto-update via painel (Fase 2) ─────

  async function carregarStatus() {
    try {
      const { data } = await api.get('/version/update/status')
      setUpdateStatus(data)
      return data
    } catch (e) {
      // Endpoints podem não existir em backend antigo — silencia.
      return null
    }
  }

  function pararPolling() {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }

  function iniciarPolling() {
    pararPolling()
    pollRef.current = setInterval(async () => {
      const data = await carregarStatus()
      if (!data || !ESTADOS_ATIVOS.has(data.state)) {
        pararPolling()
        // Quando termina, recarrega a checagem de versão (current pode ter mudado)
        if (data && data.state === 'success') carregar({ refresh: true })
      }
    }, POLL_INTERVAL_MS)
  }

  useEffect(() => {
    carregarStatus().then(data => {
      if (data && ESTADOS_ATIVOS.has(data.state)) iniciarPolling()
    })
    return pararPolling
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function disparar() {
    if (!info?.target) return
    setTriggerErro('')
    setTriggering(true)
    try {
      const { data } = await api.post('/version/update/trigger', {
        target_tag: info.target.tag,
        confirm_version: confirmText.trim(),
      })
      setUpdateStatus(data)
      setShowConfirm(false)
      setConfirmText('')
      iniciarPolling()
    } catch (e) {
      setTriggerErro(e.response?.data?.detail || e.message || 'Falha ao disparar update')
    } finally {
      setTriggering(false)
    }
  }

  if (loading) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 flex items-center gap-2 text-slate-400">
        <Loader2 size={16} className="animate-spin" /> Carregando informações de versão…
      </div>
    )
  }

  if (!info) {
    return (
      <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 text-sm text-slate-400">
        Não foi possível consultar a versão. {erro && <span className="text-red-400">({erro})</span>}
      </div>
    )
  }

  const semGithubData = !info.latest_lts && !info.latest_edge
  const semReleases = !semGithubData && info.target && !info.target.published_at
  // Comando SSH pronto pra copiar — usa a tag alvo se houver, senão deixa placeholder.
  const tagAlvo = info.target?.tag || `v${info.current}`
  const comandoSsh = `cd /root/NEXUS-BETA && git fetch origin && git checkout ${tagAlvo} && docker compose up -d --build backend frontend`

  return (
    <div className="bg-slate-800 border border-slate-700 rounded-2xl p-6 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <GitBranch size={18} className="text-sky-400" />
          <h2 className="font-semibold text-white">Atualização do sistema</h2>
        </div>
        <button onClick={() => carregar({ refresh: true })} disabled={refreshing}
          className="text-xs text-slate-400 hover:text-white flex items-center gap-1.5 px-2 py-1 rounded hover:bg-slate-700 disabled:opacity-50">
          <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} /> Atualizar agora
        </button>
      </div>

      {/* Estado atual */}
      <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-4 space-y-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">Você está em</span>
            <code className="text-sky-300 font-mono">v{info.current}</code>
            {info.current_release && <ChannelBadge channel={info.current_release.is_lts ? 'lts' : 'edge'} size="xs" />}
          </div>
          {info.current_dias_em_producao != null && (
            <span className="text-xs text-slate-400">
              {info.current_dias_em_producao === 0
                ? 'publicada hoje'
                : `há ${info.current_dias_em_producao} dia${info.current_dias_em_producao === 1 ? '' : 's'} em produção`}
            </span>
          )}
        </div>
      </div>

      {/* Canal */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-slate-300">Canal de atualização</span>
          {!isMaster && <span className="text-xs text-slate-500">(só admin master pode trocar)</span>}
        </div>
        <div className="flex gap-2">
          {['lts', 'edge'].map(c => {
            const ativo = info.channel === c
            const Icon = c === 'lts' ? ShieldCheck : FlaskConical
            return (
              <button key={c} onClick={() => trocarCanal(c)} disabled={!isMaster || savingChannel}
                className={`flex-1 flex items-center gap-2 px-3 py-2 rounded-lg border text-sm transition-colors ${
                  ativo
                    ? (c === 'lts'
                        ? 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300'
                        : 'border-amber-500/50 bg-amber-500/10 text-amber-300')
                    : 'border-slate-700 bg-slate-900/40 text-slate-400 hover:bg-slate-700/30'
                } ${(!isMaster || savingChannel) && !ativo ? 'opacity-50 cursor-not-allowed' : ''}`}>
                <Icon size={14} />
                <span className="font-medium">{c === 'lts' ? 'LTS' : 'Edge'}</span>
                <span className="text-xs opacity-70 hidden sm:inline">
                  {c === 'lts' ? '· só estáveis' : '· últimas + testes'}
                </span>
              </button>
            )
          })}
        </div>
        <p className="text-[11px] text-slate-500 mt-1.5">
          {info.channel === 'lts'
            ? 'Recebendo só releases marcadas como "Latest" no GitHub (promovidas após estabilizarem em produção).'
            : 'Recebendo a release mais recente, incluindo as marcadas como "Pre-release" (features novas em teste).'}
        </p>
      </div>

      {/* Lista de últimas releases por canal */}
      <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-4 divide-y divide-slate-700">
        <ReleaseLine label="Última LTS" info={info.latest_lts} />
        <ReleaseLine label="Última Edge" info={info.latest_edge} />
      </div>

      {/* Aviso quando GitHub não tem release / sem token */}
      {semGithubData && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-200 flex gap-2">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" />
          <span>
            Sem dados do GitHub. Verifique <code className="bg-slate-900 px-1 rounded">GITHUB_TOKEN</code> e
            <code className="bg-slate-900 px-1 rounded ml-1">GITHUB_REPO</code> no <code className="bg-slate-900 px-1 rounded">.env</code>,
            e crie pelo menos um <strong>Release</strong> no GitHub.
          </span>
        </div>
      )}
      {semReleases && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-200 flex gap-2">
          <Info size={14} className="shrink-0 mt-0.5" />
          <span>
            O repositório ainda não tem GitHub Releases (apenas tags) — canais não distinguem
            estáveis/testes até você criar Releases. Vá em GitHub → Releases → "Draft a new release".
          </span>
        </div>
      )}

      {/* Painel de status do último update (Fase 2) */}
      {updateStatus && updateStatus.state !== 'idle' && (
        <UpdateStatusPanel status={updateStatus} />
      )}

      {/* Card de "tem update disponível" */}
      {info.update_available && info.target && (
        <div className="bg-sky-500/5 border border-sky-500/30 rounded-lg overflow-hidden">
          <div className="p-4 flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              <Sparkles size={16} className="text-sky-400" />
              <span className="text-sky-200 font-medium">
                Versão <code className="font-mono">{info.target.tag}</code> disponível
              </span>
              <ChannelBadge channel={info.target.is_lts ? 'lts' : 'edge'} size="xs" />
              <span className="text-xs text-slate-400">publicada em {formatarData(info.target.published_at)}</span>
            </div>
            <button onClick={() => setChangelogOpen(o => !o)}
              className="text-xs flex items-center gap-1 text-sky-300 hover:text-sky-200">
              {changelogOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              {changelogOpen ? 'Recolher' : 'Ver mudanças e como atualizar'}
            </button>
          </div>

          {changelogOpen && (
            <div className="border-t border-sky-500/20 p-4 space-y-3 bg-slate-900/40">
              {info.target.body ? (
                <div>
                  <p className="text-xs text-slate-400 mb-1">O que muda em {info.target.tag}:</p>
                  <pre className="bg-slate-900 rounded-lg p-3 text-xs text-slate-300 whitespace-pre-wrap break-words font-mono leading-relaxed max-h-96 overflow-auto">
                    {info.target.body}
                  </pre>
                </div>
              ) : (
                <p className="text-xs text-slate-500 italic">
                  Sem notas de release no GitHub. Veja o CHANGELOG.md no repositório.
                </p>
              )}
              <div>
                <p className="text-xs text-slate-400 mb-1">Comando para atualizar (SSH no servidor):</p>
                <div className="flex gap-2">
                  <code className="flex-1 bg-slate-900 border border-slate-700 rounded p-2 text-xs text-emerald-300 font-mono overflow-x-auto whitespace-nowrap">
                    {comandoSsh}
                  </code>
                  <button onClick={() => copiar(comandoSsh, 'cmd')}
                    title="Copiar comando"
                    className="shrink-0 px-2.5 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-slate-200">
                    {copiado === 'cmd' ? <Check size={14} /> : <Copy size={14} />}
                  </button>
                </div>
                <p className="text-[11px] text-slate-500 mt-1.5">
                  Cole no terminal SSH (porta 2288) do servidor.
                </p>
              </div>

              {/* Botão: Atualizar pelo painel (Fase 2) */}
              {isMaster && (
                <div className="border-t border-sky-500/20 pt-3">
                  {updateStatus && !updateStatus.host_helper_disponivel ? (
                    <div className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded p-2 flex gap-2">
                      <Info size={13} className="shrink-0 mt-0.5" />
                      <span>
                        Helper de update do host não está instalado. Conecte por SSH e rode uma vez:{' '}
                        <code className="bg-slate-900 px-1 rounded">cd /root/NEXUS-BETA &amp;&amp; bash scripts/setup-update-helper.sh</code>.
                        Depois disso, este botão fica disponível.
                      </span>
                    </div>
                  ) : updateStatus && ESTADOS_ATIVOS.has(updateStatus.state) ? (
                    <div className="text-xs text-sky-200 flex items-center gap-2">
                      <Loader2 size={13} className="animate-spin" />
                      Update em andamento ({updateStatus.state}) — aguarde concluir.
                    </div>
                  ) : (
                    <button onClick={() => { setConfirmText(''); setTriggerErro(''); setShowConfirm(true) }}
                      className="flex items-center gap-2 px-3 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium">
                      <Rocket size={14} /> Atualizar pelo painel
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Modal de confirmação — Fase 2 */}
      {showConfirm && info?.target && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={triggering ? undefined : () => setShowConfirm(false)}>
          <div className="bg-slate-800 rounded-lg border border-emerald-500/40 w-full max-w-md" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-slate-700">
              <h3 className="font-semibold text-emerald-300 flex items-center gap-2"><Rocket size={18} /> Confirmar atualização</h3>
              <button onClick={() => setShowConfirm(false)} disabled={triggering}
                className="text-slate-400 hover:text-white disabled:opacity-40"><X size={18} /></button>
            </div>
            <div className="p-4 space-y-3">
              <p className="text-sm text-slate-300">
                Você vai atualizar de <code className="text-sky-300 font-mono">v{info.current}</code> para <code className="text-emerald-300 font-mono">{info.target.tag}</code>.
              </p>
              <div className="text-xs text-amber-200 bg-amber-500/10 border border-amber-500/30 rounded p-2">
                <AlertTriangle size={13} className="inline mr-1" />
                Durante o build os containers <strong>backend</strong> e <strong>frontend</strong> reiniciam (~1-3 min).
                O painel pode ficar indisponível por alguns segundos.
              </div>
              <div>
                <label className="text-xs text-slate-400 block mb-1">
                  Para confirmar, digite a versão alvo: <code className="text-emerald-300 font-mono">{info.target.tag}</code>
                </label>
                <input value={confirmText} onChange={e => setConfirmText(e.target.value)} disabled={triggering}
                  placeholder={info.target.tag}
                  className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-white font-mono" />
              </div>
              {triggerErro && (
                <div className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 flex gap-2">
                  <AlertTriangle size={14} className="shrink-0 mt-0.5" /> <span>{triggerErro}</span>
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 p-4 border-t border-slate-700">
              <button onClick={() => setShowConfirm(false)} disabled={triggering}
                className="px-3 py-1.5 text-sm text-slate-300 hover:text-white">Cancelar</button>
              <button onClick={disparar} disabled={triggering || confirmText.trim() !== info.target.tag}
                className="px-4 py-1.5 text-sm bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded font-medium flex items-center gap-1.5">
                {triggering ? <Loader2 size={14} className="animate-spin" /> : <Rocket size={14} />}
                {triggering ? 'Disparando' : 'Atualizar agora'}
              </button>
            </div>
          </div>
        </div>
      )}

      {erro && (
        <div className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 flex gap-2">
          <AlertTriangle size={14} className="shrink-0 mt-0.5" /> <span>{erro}</span>
        </div>
      )}
    </div>
  )
}
