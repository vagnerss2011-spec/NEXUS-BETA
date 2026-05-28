import { useEffect, useState } from 'react'
import {
  RefreshCw, GitBranch, Copy, Check, ExternalLink, Info, Loader2,
  Sparkles, ShieldCheck, FlaskConical, AlertTriangle, ChevronDown, ChevronUp,
} from 'lucide-react'
import api, { getUser } from '../services/api'

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
                  Cole no terminal SSH (porta 2288) do servidor. O update via botão no painel chegará numa próxima versão.
                </p>
              </div>
            </div>
          )}
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
