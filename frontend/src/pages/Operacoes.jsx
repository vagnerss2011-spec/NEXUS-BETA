import { useEffect, useMemo, useState } from 'react'
import {
  Wrench, Search, CheckCircle, XCircle, Play, Loader2, AlertTriangle,
  History, ChevronDown, ChevronRight, Terminal, Info, Shield, RefreshCw
} from 'lucide-react'
import api, { getUser } from '../services/api'

// Catálogo de ações renderizado na UI. Slug bate com o ACOES dict do backend
// (services/mikrotik_bulk.py). Esse mapa controla:
//  - label/descrição mostradas no dropdown
//  - se exige confirmação dupla (destrutivas: remover_user, comando_livre)
//  - schema do form de parâmetros
const ACOES_CATALOGO = {
  checar_versao: {
    label: 'Checar versão RouterOS',
    descricao: 'Read-only. Mostra versão de firmware, board e arquitetura.',
    categoria: 'check',
    params: [],
  },
  listar_usuarios: {
    label: 'Listar usuários ativos',
    descricao: 'Read-only. Nome + group + last-logged-in de cada usuário.',
    categoria: 'check',
    params: [],
  },
  inventario: {
    label: 'Inventário (modelo + uptime + /file livre)',
    descricao: 'Read-only. Snapshot de saúde dos devices em uma execução.',
    categoria: 'check',
    params: [],
  },
  adicionar_user: {
    label: 'Adicionar usuário',
    descricao: 'Write. Cria usuário com group/senha definidos.',
    categoria: 'config',
    params: [
      { key: 'username', label: 'Usuário', type: 'text', placeholder: 'ex: monitor', required: true },
      { key: 'password', label: 'Senha (mín 8 chars)', type: 'password', placeholder: 'min 8 caracteres', required: true },
      {
        key: 'group', label: 'Grupo', type: 'select', required: true, default: 'read',
        options: [
          { value: 'read', label: 'read (só leitura)' },
          { value: 'write', label: 'write (leitura + escrita)' },
          { value: 'full', label: 'full (todas as permissões)' },
        ],
      },
    ],
  },
  remover_user: {
    label: 'Remover usuário',
    descricao: 'Write DESTRUTIVO. Remove usuário do device pelo nome. Confirmação dupla.',
    categoria: 'destrutivo',
    params: [
      { key: 'username', label: 'Usuário a remover', type: 'text', placeholder: 'ex: convidado', required: true },
    ],
  },
  configurar_snmp: {
    label: 'Configurar SNMP',
    descricao: 'Write idempotente. Habilita SNMP + community/trap-target.',
    categoria: 'config',
    params: [
      { key: 'community', label: 'Community', type: 'text', placeholder: 'ex: public', required: true },
      { key: 'contact', label: 'Contact (opcional)', type: 'text', placeholder: 'ex: noc@empresa.com' },
      { key: 'location', label: 'Location (opcional)', type: 'text', placeholder: 'ex: POP-Centro' },
      { key: 'trap_target', label: 'Trap target IP (opcional)', type: 'text', placeholder: 'ex: 10.0.0.50' },
    ],
  },
  cleanup_orfaos: {
    label: 'Cleanup arquivos nexus-api-*.rsc',
    descricao: 'Write idempotente. Remove órfãos da NAND deixados por coletas anteriores.',
    categoria: 'config',
    params: [],
  },
  comando_livre: {
    label: '⚠ Comando livre (admin master)',
    descricao: 'Executa comando arbitrário via API. Bloqueia padrões destrutivos. Confirmação dupla.',
    categoria: 'destrutivo',
    params: [
      { key: 'comando', label: 'Comando (inicia com /)', type: 'text', placeholder: '/system/identity/print', required: true },
      { key: 'args_json', label: 'Args JSON (opcional)', type: 'textarea', placeholder: '{"detail": ""}' },
    ],
  },
}

function ActionBadge({ categoria }) {
  if (categoria === 'check') {
    return <span className="text-[10px] uppercase tracking-wider bg-sky-500/15 text-sky-300 px-1.5 py-0.5 rounded font-bold">CHECK</span>
  }
  if (categoria === 'config') {
    return <span className="text-[10px] uppercase tracking-wider bg-amber-500/15 text-amber-300 px-1.5 py-0.5 rounded font-bold">CONFIG</span>
  }
  return <span className="text-[10px] uppercase tracking-wider bg-red-500/15 text-red-300 px-1.5 py-0.5 rounded font-bold">DESTRUTIVO</span>
}

export default function Operacoes() {
  const me = getUser()
  const isMaster = me?.role === 'admin'

  const [devices, setDevices] = useState([])
  const [selecionados, setSelecionados] = useState(() => new Set())
  const [busca, setBusca] = useState('')
  const [acao, setAcao] = useState('checar_versao')
  const [params, setParams] = useState({})
  const [executando, setExecutando] = useState(false)
  const [resultado, setResultado] = useState(null)
  const [confirmacao, setConfirmacao] = useState(null) // texto digitado no modal
  const [mostrarConfirm, setMostrarConfirm] = useState(false)
  const [mostrarHistorico, setMostrarHistorico] = useState(false)
  const [historico, setHistorico] = useState([])
  const [detalheOp, setDetalheOp] = useState(null)

  // Carrega devices Mikrotik (v6/v7) com protocolo API — únicos elegíveis.
  useEffect(() => {
    (async () => {
      try {
        const r = await api.get('/devices/')
        const mikrotiks = r.data.filter(d =>
          (d.fabricante === 'mikrotik' || d.fabricante === 'mikrotik_v7') &&
          d.protocolo === 'api' &&
          d.ativo
        )
        setDevices(mikrotiks)
      } catch (e) {
        console.error('falha carregando devices', e)
      }
    })()
  }, [])

  // Aplica os defaults dos params ao mudar de ação.
  useEffect(() => {
    const def = {}
    const cfg = ACOES_CATALOGO[acao]
    if (cfg) cfg.params.forEach(p => { if (p.default !== undefined) def[p.key] = p.default })
    setParams(def)
    setResultado(null)
  }, [acao])

  // Filtra ações disponíveis: comando livre só pra master.
  const acoesDisponiveis = useMemo(() => {
    return Object.entries(ACOES_CATALOGO).filter(([slug, _cfg]) => {
      if (slug === 'comando_livre' && !isMaster) return false
      return true
    })
  }, [isMaster])

  const devicesFiltrados = useMemo(() => {
    const q = busca.trim().toLowerCase()
    if (!q) return devices
    return devices.filter(d =>
      d.nome.toLowerCase().includes(q) || d.ip.toLowerCase().includes(q)
    )
  }, [devices, busca])

  function toggleDevice(id) {
    setSelecionados(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleTodos() {
    if (selecionados.size === devicesFiltrados.length) {
      setSelecionados(new Set())
    } else {
      setSelecionados(new Set(devicesFiltrados.map(d => d.id)))
    }
  }

  const cfg = ACOES_CATALOGO[acao]
  const isDestrutivo = cfg?.categoria === 'destrutivo'
  const palavraConfirm = acao === 'comando_livre' ? 'EXECUTAR' : 'CONFIRMAR'

  function clickExecutar() {
    if (selecionados.size === 0) return
    // Validação client-side dos required
    const faltando = (cfg?.params || []).filter(p => p.required && !params[p.key]?.toString().trim())
    if (faltando.length > 0) {
      alert(`Preencha: ${faltando.map(p => p.label).join(', ')}`)
      return
    }
    if (isDestrutivo) {
      setConfirmacao('')
      setMostrarConfirm(true)
    } else {
      executar()
    }
  }

  async function executar() {
    setMostrarConfirm(false)
    setExecutando(true)
    setResultado(null)
    try {
      // Pra comando_livre, parse do args_json
      let paramsFinal = { ...params }
      if (acao === 'comando_livre' && paramsFinal.args_json) {
        try {
          paramsFinal.args = JSON.parse(paramsFinal.args_json)
        } catch {
          alert('Args JSON inválido — precisa ser JSON válido tipo {"chave":"valor"}')
          setExecutando(false)
          return
        }
        delete paramsFinal.args_json
      }
      const r = await api.post('/mikrotik-bulk/executar', {
        acao,
        device_ids: Array.from(selecionados),
        params: paramsFinal,
      })
      setResultado(r.data)
    } catch (e) {
      const detail = e.response?.data?.detail
      alert(typeof detail === 'string' ? detail : (detail?.message || 'Erro ao executar'))
    } finally {
      setExecutando(false)
    }
  }

  async function abrirHistorico() {
    setMostrarHistorico(true)
    try {
      const r = await api.get('/mikrotik-bulk/historico?limit=50')
      setHistorico(r.data)
    } catch (e) {
      console.error('falha carregando histórico', e)
    }
  }

  async function abrirDetalhe(opId) {
    try {
      const r = await api.get(`/mikrotik-bulk/historico/${opId}`)
      setDetalheOp(r.data)
    } catch (e) {
      console.error('falha carregando detalhe', e)
    }
  }

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex items-start justify-between mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Wrench className="text-amber-400" /> Operações Mikrotik em Massa
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Execute ações em N devices selecionados. Só Mikrotik (v6/v7) com protocolo API.
          </p>
        </div>
        <button onClick={abrirHistorico}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors">
          <History size={16} /> Histórico
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Coluna esquerda: seleção de devices */}
        <div className="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-lg p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold text-white">1. Selecione devices</h2>
            <span className="text-xs text-slate-400">{selecionados.size} de {devices.length} selecionado(s)</span>
          </div>
          <div className="relative mb-3">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              placeholder="Filtrar por nome ou IP..."
              value={busca}
              onChange={e => setBusca(e.target.value)}
              className="w-full bg-slate-900 text-white pl-9 pr-3 py-2 rounded-md border border-slate-700 focus:border-sky-500 focus:outline-none text-sm"
            />
          </div>
          <div className="mb-2">
            <button onClick={toggleTodos}
              className="text-xs text-sky-400 hover:text-sky-300">
              {selecionados.size === devicesFiltrados.length && devicesFiltrados.length > 0
                ? 'Desmarcar todos visíveis' : 'Marcar todos visíveis'}
            </button>
          </div>
          <div className="max-h-96 overflow-y-auto border border-slate-700 rounded-md divide-y divide-slate-700">
            {devicesFiltrados.length === 0 && (
              <div className="p-4 text-center text-sm text-slate-400">
                {devices.length === 0
                  ? 'Nenhum device Mikrotik com protocolo API cadastrado nesta empresa.'
                  : 'Nenhum device bate com o filtro.'}
              </div>
            )}
            {devicesFiltrados.map(d => (
              <label key={d.id}
                className="flex items-center gap-3 p-2.5 hover:bg-slate-700/40 cursor-pointer">
                <input type="checkbox"
                  checked={selecionados.has(d.id)}
                  onChange={() => toggleDevice(d.id)}
                  className="w-4 h-4 accent-sky-500" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-white truncate">{d.nome}</div>
                  <div className="text-xs text-slate-400">
                    {d.ip}:{d.porta} · {d.fabricante === 'mikrotik_v7' ? 'RouterOS v7' : 'RouterOS v6'}
                    {d.api_tls && ' · TLS'}
                  </div>
                </div>
              </label>
            ))}
          </div>
        </div>

        {/* Coluna direita: ação + params + executar */}
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-5 space-y-4">
          <div>
            <h2 className="text-lg font-semibold text-white mb-3">2. Escolha a ação</h2>
            <select value={acao} onChange={e => setAcao(e.target.value)}
              className="w-full bg-slate-900 text-white px-3 py-2 rounded-md border border-slate-700 focus:border-sky-500 focus:outline-none text-sm">
              {acoesDisponiveis.map(([slug, c]) => (
                <option key={slug} value={slug}>{c.label}</option>
              ))}
            </select>
            <div className="mt-2 flex items-start gap-2 text-xs text-slate-400">
              <ActionBadge categoria={cfg?.categoria} />
              <span className="flex-1">{cfg?.descricao}</span>
            </div>
          </div>

          {/* Form dinâmico */}
          {cfg?.params?.length > 0 && (
            <div className="space-y-2 pt-2 border-t border-slate-700">
              <h3 className="text-sm font-semibold text-slate-300">Parâmetros</h3>
              {cfg.params.map(p => (
                <div key={p.key}>
                  <label className="block text-xs text-slate-400 mb-1">
                    {p.label} {p.required && <span className="text-red-400">*</span>}
                  </label>
                  {p.type === 'select' ? (
                    <select value={params[p.key] || p.default || ''}
                      onChange={e => setParams({ ...params, [p.key]: e.target.value })}
                      className="w-full bg-slate-900 text-white px-3 py-2 rounded-md border border-slate-700 focus:border-sky-500 focus:outline-none text-sm">
                      {p.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  ) : p.type === 'textarea' ? (
                    <textarea value={params[p.key] || ''}
                      onChange={e => setParams({ ...params, [p.key]: e.target.value })}
                      placeholder={p.placeholder}
                      rows={3}
                      className="w-full bg-slate-900 text-white px-3 py-2 rounded-md border border-slate-700 focus:border-sky-500 focus:outline-none text-sm font-mono"
                    />
                  ) : (
                    <input type={p.type}
                      value={params[p.key] || ''}
                      onChange={e => setParams({ ...params, [p.key]: e.target.value })}
                      placeholder={p.placeholder}
                      className="w-full bg-slate-900 text-white px-3 py-2 rounded-md border border-slate-700 focus:border-sky-500 focus:outline-none text-sm"
                    />
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="pt-2 border-t border-slate-700">
            <button onClick={clickExecutar}
              disabled={executando || selecionados.size === 0}
              className={`w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-md font-medium text-sm transition-colors ${
                isDestrutivo
                  ? 'bg-red-600 hover:bg-red-500 disabled:bg-slate-700 text-white'
                  : 'bg-sky-600 hover:bg-sky-500 disabled:bg-slate-700 text-white'
              } disabled:cursor-not-allowed disabled:text-slate-400`}>
              {executando ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
              {executando ? 'Executando...' : `Executar em ${selecionados.size} device(s)`}
            </button>
            {isDestrutivo && (
              <p className="text-xs text-red-300 mt-2 flex items-center gap-1">
                <Shield size={12} /> Ação destrutiva — exige confirmação
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Resultado */}
      {resultado && <ResultadoView resultado={resultado} />}

      {/* Modal de confirmação dupla */}
      {mostrarConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 border border-red-500/30 rounded-lg p-6 max-w-md w-full">
            <div className="flex items-center gap-3 mb-3">
              <AlertTriangle className="text-red-400" size={24} />
              <h3 className="text-lg font-bold text-white">Confirmação obrigatória</h3>
            </div>
            <p className="text-sm text-slate-300 mb-2">
              Você está prestes a executar <span className="font-bold text-red-300">{cfg.label}</span> em <span className="font-bold text-white">{selecionados.size} device(s)</span>.
            </p>
            <p className="text-sm text-slate-400 mb-4">
              Esta ação NÃO pode ser desfeita pelo painel. Digite <code className="bg-slate-900 px-1.5 py-0.5 rounded text-amber-300">{palavraConfirm}</code> para prosseguir.
            </p>
            <input type="text" value={confirmacao}
              onChange={e => setConfirmacao(e.target.value)}
              placeholder={palavraConfirm}
              autoFocus
              className="w-full bg-slate-900 text-white px-3 py-2 rounded-md border border-slate-700 focus:border-red-500 focus:outline-none mb-4 font-mono text-sm" />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setMostrarConfirm(false)}
                className="px-4 py-2 text-sm text-slate-300 hover:bg-slate-700 rounded-md">
                Cancelar
              </button>
              <button onClick={executar}
                disabled={confirmacao !== palavraConfirm}
                className="px-4 py-2 text-sm bg-red-600 hover:bg-red-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white rounded-md font-medium">
                Executar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal de histórico */}
      {mostrarHistorico && (
        <HistoricoModal historico={historico} onClose={() => { setMostrarHistorico(false); setDetalheOp(null) }}
          onAbrir={abrirDetalhe} detalhe={detalheOp} onFecharDetalhe={() => setDetalheOp(null)}
          devices={devices} />
      )}
    </div>
  )
}

function ResultadoView({ resultado }) {
  const [expandidos, setExpandidos] = useState(() => new Set())

  function toggle(id) {
    setExpandidos(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className="mt-6 bg-slate-800 border border-slate-700 rounded-lg p-5">
      <h2 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
        <Terminal size={18} /> Resultado da execução #{resultado.operacao_id}
      </h2>
      <div className="grid grid-cols-3 gap-3 mb-4 text-center">
        <div className="bg-slate-900 rounded p-3">
          <div className="text-2xl font-bold text-white">{resultado.total}</div>
          <div className="text-xs text-slate-400 uppercase tracking-wider">Total</div>
        </div>
        <div className="bg-emerald-500/10 rounded p-3">
          <div className="text-2xl font-bold text-emerald-300">{resultado.sucessos}</div>
          <div className="text-xs text-emerald-400 uppercase tracking-wider">Sucesso</div>
        </div>
        <div className="bg-red-500/10 rounded p-3">
          <div className="text-2xl font-bold text-red-300">{resultado.falhas}</div>
          <div className="text-xs text-red-400 uppercase tracking-wider">Falha</div>
        </div>
      </div>
      <p className="text-xs text-slate-400 mb-3">
        Duração total: {(resultado.duracao_ms / 1000).toFixed(1)}s
      </p>
      <div className="border border-slate-700 rounded divide-y divide-slate-700 max-h-[500px] overflow-y-auto">
        {/* Falhas primeiro (mesma lógica do scheduler) */}
        {[...resultado.resultados].sort((a, b) => {
          if (a.status === 'falha' && b.status !== 'falha') return -1
          if (a.status !== 'falha' && b.status === 'falha') return 1
          return 0
        }).map(r => (
          <div key={r.device_id} className="bg-slate-900/40">
            <button onClick={() => toggle(r.device_id)}
              className="w-full flex items-center gap-3 p-3 text-left hover:bg-slate-700/30">
              {expandidos.has(r.device_id)
                ? <ChevronDown size={16} className="text-slate-400" />
                : <ChevronRight size={16} className="text-slate-400" />}
              {r.status === 'sucesso'
                ? <CheckCircle size={16} className="text-emerald-400" />
                : <XCircle size={16} className="text-red-400" />}
              <span className="text-sm text-white flex-1">{r.device_nome || `#${r.device_id}`}</span>
              <span className="text-xs text-slate-400">{r.duracao_ms}ms</span>
            </button>
            {expandidos.has(r.device_id) && (
              <pre className="px-10 pb-3 text-xs text-slate-300 whitespace-pre-wrap font-mono bg-slate-950/40">
                {r.output || '(sem output)'}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function HistoricoModal({ historico, onClose, onAbrir, detalhe, onFecharDetalhe, devices }) {
  const devicesMap = useMemo(() => {
    const m = {}
    devices.forEach(d => { m[d.id] = d.nome })
    return m
  }, [devices])

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-800 border border-slate-700 rounded-lg w-full max-w-5xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-slate-700">
          <h3 className="text-lg font-bold text-white flex items-center gap-2">
            <History size={20} /> Histórico de operações
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white">✕</button>
        </div>

        {detalhe ? (
          <div className="flex-1 overflow-y-auto p-4">
            <button onClick={onFecharDetalhe}
              className="text-xs text-sky-400 hover:text-sky-300 mb-3">← Voltar à lista</button>
            <div className="mb-3">
              <h4 className="text-base font-bold text-white">{ACOES_CATALOGO[detalhe.acao]?.label || detalhe.acao}</h4>
              <p className="text-xs text-slate-400">
                Por {detalhe.usuario_nome} em {new Date(detalhe.iniciado_em).toLocaleString()} · {detalhe.total} devices · {detalhe.sucessos} sucesso(s) · {detalhe.falhas} falha(s)
              </p>
              {Object.keys(detalhe.params || {}).length > 0 && (
                <details className="mt-2">
                  <summary className="text-xs text-slate-400 cursor-pointer">Params</summary>
                  <pre className="text-xs bg-slate-900 p-2 mt-1 rounded">{JSON.stringify(detalhe.params, null, 2)}</pre>
                </details>
              )}
            </div>
            <div className="border border-slate-700 rounded divide-y divide-slate-700">
              {Object.entries(detalhe.resultados || {}).sort((a, b) => {
                if (a[1].status === 'falha' && b[1].status !== 'falha') return -1
                if (a[1].status !== 'falha' && b[1].status === 'falha') return 1
                return 0
              }).map(([devId, r]) => (
                <div key={devId} className="p-3 bg-slate-900/40">
                  <div className="flex items-center gap-2 mb-1">
                    {r.status === 'sucesso'
                      ? <CheckCircle size={14} className="text-emerald-400" />
                      : <XCircle size={14} className="text-red-400" />}
                    <span className="text-sm text-white">{devicesMap[devId] || `Device #${devId}`}</span>
                    <span className="text-xs text-slate-400 ml-auto">{r.duracao_ms}ms</span>
                  </div>
                  <pre className="text-xs text-slate-300 whitespace-pre-wrap font-mono pl-6">{r.output}</pre>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto">
            {historico.length === 0 && (
              <div className="p-8 text-center text-slate-400 text-sm">Nenhuma execução registrada ainda.</div>
            )}
            {historico.map(h => (
              <button key={h.id} onClick={() => onAbrir(h.id)}
                className="w-full text-left p-3 border-b border-slate-700 hover:bg-slate-700/40">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-white truncate">{ACOES_CATALOGO[h.acao]?.label || h.acao}</div>
                    <div className="text-xs text-slate-400">
                      {h.usuario_nome} · {new Date(h.iniciado_em).toLocaleString()}
                    </div>
                  </div>
                  <div className="text-right shrink-0">
                    <div className="text-xs text-slate-400">{h.total} devices</div>
                    <div className="text-xs">
                      <span className="text-emerald-400">{h.sucessos} ✓</span>
                      {h.falhas > 0 && <span className="text-red-400 ml-1">{h.falhas} ✗</span>}
                    </div>
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
