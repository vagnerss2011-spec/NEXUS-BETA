import { useEffect, useState } from 'react'
import {
  HardDrive, Upload, Download, Trash2, KeyRound, RefreshCw, Plus,
  Copy, Check, Eye, EyeOff, AlertTriangle, FileQuestion, Power, Loader2,
  Server, Info, X, Edit3,
} from 'lucide-react'
import api, { getUser } from '../services/api'

// Catálogo de fabricantes pra dropdown — strings livres (DB aceita qualquer),
// mas pre-populamos com os comuns pra evitar variações tipo "MikroTik" vs "Mikrotik".
const FABRICANTES_COMUNS = [
  'Mikrotik', 'Huawei', 'Ubiquiti', 'Intelbras', 'Datacom',
  'Cisco', 'Juniper', 'ZTE', 'Nokia', 'Fiberhome', 'VSolutions', 'Outro',
]

function humanizarBytes(n) {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatarData(s) {
  if (!s) return '—'
  return new Date(s).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}

// ────────────────────────── Modal de upload ──────────────────────────
function UploadModal({ open, onClose, onSucesso }) {
  const [arquivo, setArquivo] = useState(null)
  const [nome, setNome] = useState('')
  const [descricao, setDescricao] = useState('')
  const [fabricante, setFabricante] = useState('')
  const [modeloAlvo, setModeloAlvo] = useState('')
  const [versao, setVersao] = useState('')
  const [progresso, setProgresso] = useState(0)
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState('')

  if (!open) return null

  async function submit(e) {
    e.preventDefault()
    setErro('')
    if (!arquivo) { setErro('Selecione um arquivo'); return }
    if (!nome.trim()) { setErro('Nome é obrigatório'); return }
    const fd = new FormData()
    fd.append('arquivo', arquivo)
    fd.append('nome', nome.trim())
    if (descricao.trim()) fd.append('descricao', descricao.trim())
    if (fabricante.trim()) fd.append('fabricante', fabricante.trim())
    if (modeloAlvo.trim()) fd.append('modelo_alvo', modeloAlvo.trim())
    if (versao.trim()) fd.append('versao', versao.trim())
    setEnviando(true)
    try {
      await api.post('/firmwares/upload', fd, {
        // Multipart sem ressetar Content-Type — axios cuida do boundary.
        // Timeout 60 min pra firmwares de 1-2GB em links lentos.
        timeout: 60 * 60 * 1000,
        onUploadProgress: (evt) => {
          if (evt.total) setProgresso(Math.round((evt.loaded * 100) / evt.total))
        },
      })
      onSucesso()
      onClose()
      reset()
    } catch (e) {
      setErro(e.response?.data?.detail || e.message || 'Falha no upload')
    } finally {
      setEnviando(false)
    }
  }

  function reset() {
    setArquivo(null); setNome(''); setDescricao(''); setFabricante('')
    setModeloAlvo(''); setVersao(''); setProgresso(0); setErro('')
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={enviando ? undefined : onClose}>
      <div className="bg-slate-800 rounded-lg border border-slate-700 w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-slate-700">
          <h3 className="font-semibold text-white flex items-center gap-2"><Upload size={18} /> Upload de firmware</h3>
          <button onClick={onClose} disabled={enviando} className="text-slate-400 hover:text-white disabled:opacity-40"><X size={18} /></button>
        </div>
        <form onSubmit={submit} className="p-4 space-y-3">
          <div>
            <label className="text-xs text-slate-400 block mb-1">Arquivo *</label>
            <input type="file" required onChange={e => setArquivo(e.target.files?.[0] || null)}
              disabled={enviando}
              className="w-full text-sm text-slate-300 file:mr-3 file:py-1.5 file:px-3 file:rounded file:border-0 file:bg-sky-500/20 file:text-sky-300 file:cursor-pointer file:font-medium" />
            {arquivo && <p className="text-[11px] text-slate-500 mt-1">{arquivo.name} · {humanizarBytes(arquivo.size)}</p>}
          </div>
          <div>
            <label className="text-xs text-slate-400 block mb-1">Nome (display) *</label>
            <input value={nome} onChange={e => setNome(e.target.value)} required disabled={enviando}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-white"
              placeholder="ex: RouterOS 7.16.2 (CCR2004)" />
          </div>
          <div>
            <label className="text-xs text-slate-400 block mb-1">Descrição</label>
            <textarea value={descricao} onChange={e => setDescricao(e.target.value)} disabled={enviando} rows={2}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-white" />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-xs text-slate-400 block mb-1">Fabricante</label>
              <input list="fabricantes-list" value={fabricante} onChange={e => setFabricante(e.target.value)} disabled={enviando}
                className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-white" />
              <datalist id="fabricantes-list">
                {FABRICANTES_COMUNS.map(f => <option key={f} value={f} />)}
              </datalist>
            </div>
            <div>
              <label className="text-xs text-slate-400 block mb-1">Modelo</label>
              <input value={modeloAlvo} onChange={e => setModeloAlvo(e.target.value)} disabled={enviando}
                className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-white" />
            </div>
            <div>
              <label className="text-xs text-slate-400 block mb-1">Versão</label>
              <input value={versao} onChange={e => setVersao(e.target.value)} disabled={enviando}
                className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-white" />
            </div>
          </div>

          {enviando && (
            <div>
              <div className="w-full bg-slate-900 rounded-full h-2">
                <div className="bg-sky-500 h-2 rounded-full transition-all" style={{ width: `${progresso}%` }} />
              </div>
              <p className="text-xs text-slate-400 mt-1 text-center">Enviando… {progresso}%</p>
            </div>
          )}

          {erro && (
            <div className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 flex gap-2">
              <AlertTriangle size={16} className="shrink-0 mt-0.5" /> <span>{erro}</span>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2 border-t border-slate-700">
            <button type="button" onClick={onClose} disabled={enviando}
              className="px-3 py-1.5 text-sm text-slate-300 hover:text-white disabled:opacity-40">Cancelar</button>
            <button type="submit" disabled={enviando}
              className="px-4 py-1.5 text-sm bg-sky-500 hover:bg-sky-600 text-white rounded font-medium flex items-center gap-1.5 disabled:opacity-60">
              {enviando ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              {enviando ? 'Enviando' : 'Enviar'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ────────────────────────── Modal mostrando senha gerada ──────────────────────────
function CredencialModal({ credencial, onClose }) {
  const [mostrarSenha, setMostrarSenha] = useState(false)
  const [copiado, setCopiado] = useState('')

  if (!credencial) return null

  function copiar(texto, label) {
    navigator.clipboard.writeText(texto)
    setCopiado(label)
    setTimeout(() => setCopiado(''), 1500)
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-slate-800 rounded-lg border border-amber-500/40 w-full max-w-md" onClick={e => e.stopPropagation()}>
        <div className="p-4 border-b border-slate-700">
          <h3 className="font-semibold text-amber-300 flex items-center gap-2"><KeyRound size={18} /> Credencial FTP</h3>
          <p className="text-xs text-slate-400 mt-1">Use no device pra baixar firmwares via FTP. Copie usuário e senha.</p>
        </div>
        <div className="p-4 space-y-3">
          <div>
            <label className="text-xs text-slate-400">Usuário</label>
            <div className="flex gap-2 mt-1">
              <code className="flex-1 bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-sky-300 font-mono">
                {credencial.usuario_ftp}
              </code>
              <button onClick={() => copiar(credencial.usuario_ftp, 'user')}
                className="px-2.5 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-slate-200">
                {copiado === 'user' ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-400">Senha</label>
            <div className="flex gap-2 mt-1">
              <code className="flex-1 bg-slate-900 border border-amber-500/40 rounded px-2.5 py-1.5 text-sm text-amber-300 font-mono">
                {mostrarSenha ? credencial.senha_ftp : '•'.repeat(credencial.senha_ftp.length)}
              </code>
              <button onClick={() => setMostrarSenha(s => !s)}
                className="px-2.5 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-slate-200">
                {mostrarSenha ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
              <button onClick={() => copiar(credencial.senha_ftp, 'senha')}
                className="px-2.5 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-slate-200">
                {copiado === 'senha' ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
          </div>
          <div className="text-xs text-slate-400 bg-slate-900/60 border border-slate-700 rounded p-2">
            <Info size={14} className="inline mr-1" />
            Você pode reabrir esta credencial a qualquer momento pelo botão da chave na listagem. "Regerar senha" troca a senha e invalida a anterior nos devices.
          </div>
        </div>
        <div className="flex justify-end p-4 border-t border-slate-700">
          <button onClick={onClose}
            className="px-4 py-1.5 text-sm bg-sky-500 hover:bg-sky-600 text-white rounded font-medium">
            Já copiei
          </button>
        </div>
      </div>
    </div>
  )
}

// ────────────────────────── Modal de criar/editar origem ──────────────────────────
// origem=null → criar (gera credencial); origem=obj → editar (não troca senha).
function OrigemModal({ open, onClose, onSucesso, origem }) {
  const editando = !!origem
  const [nome, setNome] = useState('')
  const [descricao, setDescricao] = useState('')
  // Lista de IPs/CIDRs em caixas separadas. Backend aceita "a, b, c" — aqui
  // a UI quebra em campos individuais (add/remove). Sempre tem ao menos 1.
  const [cidrs, setCidrs] = useState([''])
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState('')

  // Preenche/reseta quando o modal abre (ou troca de origem). useEffect porque
  // o componente não desmonta entre aberturas — sem isso ficaria o estado velho.
  useEffect(() => {
    if (!open) return
    setNome(origem?.nome || '')
    setDescricao(origem?.descricao || '')
    const lista = (origem?.origem_cidr || '')
      .split(',').map(s => s.trim()).filter(Boolean)
    setCidrs(lista.length ? lista : [''])
    setErro('')
  }, [open, origem])

  if (!open) return null

  const setCidr = (i, v) => setCidrs(prev => prev.map((c, idx) => idx === i ? v : c))
  const addCidr = () => setCidrs(prev => [...prev, ''])
  const removeCidr = (i) => setCidrs(prev => prev.length === 1 ? [''] : prev.filter((_, idx) => idx !== i))

  async function submit(e) {
    e.preventDefault()
    setErro('')
    if (!nome.trim()) { setErro('Nome é obrigatório'); return }
    const cidrJoin = cidrs.map(c => c.trim()).filter(Boolean).join(', ')
    setEnviando(true)
    try {
      if (editando) {
        await api.patch(`/firmware-origens/${origem.id}`, {
          nome: nome.trim(),
          descricao: descricao.trim() || null,
          origem_cidr: cidrJoin,   // "" limpa a whitelist
        })
        onSucesso(null)            // edição não retorna credencial
      } else {
        const r = await api.post('/firmware-origens', {
          nome: nome.trim(),
          descricao: descricao.trim() || null,
          origem_cidr: cidrJoin || null,
        })
        onSucesso(r.data)          // entrega credencial com senha pra exibir
      }
      onClose()
    } catch (e) {
      setErro(e.response?.data?.detail || e.message || 'Falha ao salvar origem')
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={enviando ? undefined : onClose}>
      <div className="bg-slate-800 rounded-lg border border-slate-700 w-full max-w-md max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-4 border-b border-slate-700 sticky top-0 bg-slate-800">
          <h3 className="font-semibold text-white flex items-center gap-2">
            {editando ? <Edit3 size={18} /> : <Plus size={18} />}
            {editando ? `Editar origem — ${origem.usuario_ftp}` : 'Nova origem FTP'}
          </h3>
          <button onClick={onClose} disabled={enviando} className="text-slate-400 hover:text-white"><X size={18} /></button>
        </div>
        <form onSubmit={submit} className="p-4 space-y-3">
          <div>
            <label className="text-xs text-slate-400 block mb-1">Nome *</label>
            <input value={nome} onChange={e => setNome(e.target.value)} required disabled={enviando}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-white"
              placeholder="ex: Filial Brasília, NOC, etc." />
          </div>
          <div>
            <label className="text-xs text-slate-400 block mb-1">Descrição</label>
            <textarea value={descricao} onChange={e => setDescricao(e.target.value)} disabled={enviando} rows={2}
              className="w-full bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-white"
              placeholder="Pra que/quem é esse acesso (opcional)" />
          </div>
          <div>
            <label className="text-xs text-slate-400 block mb-1">IP(s) de origem — whitelist (opcional)</label>
            <div className="space-y-2">
              {cidrs.map((c, i) => (
                <div key={i} className="flex gap-2">
                  <input value={c} onChange={e => setCidr(i, e.target.value)} disabled={enviando}
                    className="flex-1 bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-sm text-white font-mono"
                    placeholder="ex: 200.1.2.3  ou  10.0.0.0/24" />
                  <button type="button" onClick={() => removeCidr(i)} disabled={enviando}
                    title="Remover este IP"
                    className="px-2.5 py-1.5 bg-slate-700 hover:bg-red-500/30 text-slate-300 hover:text-red-300 rounded">
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
            <button type="button" onClick={addCidr} disabled={enviando}
              className="mt-2 text-xs text-sky-400 hover:text-sky-300 flex items-center gap-1">
              <Plus size={13} /> Adicionar outro IP/CIDR
            </button>
            <p className="text-[11px] text-slate-500 mt-1">
              Se preenchido, o FTP só aceita conexões desses IPs. Vazio = qualquer IP (só a senha protege). Libere o(s) mesmo(s) IP(s) no firewall do servidor.
            </p>
          </div>
          {!editando && (
            <p className="text-xs text-slate-500 flex items-start gap-1.5">
              <Info size={13} className="shrink-0 mt-0.5" />
              O usuário (formato <code className="bg-slate-900 px-1 rounded">fwm_NNNNN</code>) e a senha de 24 chars serão gerados automaticamente.
            </p>
          )}
          {erro && (
            <div className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2 flex gap-2">
              <AlertTriangle size={16} className="shrink-0 mt-0.5" /> <span>{erro}</span>
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2 border-t border-slate-700">
            <button type="button" onClick={onClose} disabled={enviando}
              className="px-3 py-1.5 text-sm text-slate-300 hover:text-white">Cancelar</button>
            <button type="submit" disabled={enviando}
              className="px-4 py-1.5 text-sm bg-sky-500 hover:bg-sky-600 text-white rounded font-medium flex items-center gap-1.5 disabled:opacity-60">
              {enviando ? <Loader2 size={14} className="animate-spin" /> : (editando ? <Edit3 size={14} /> : <KeyRound size={14} />)}
              {editando ? 'Salvar' : 'Criar e gerar credencial'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ────────────────────────── Card de exemplo de comando ──────────────────────────
function ExemploComandoCard({ origens }) {
  const [serverInfo, setServerInfo] = useState({ ftp_endpoint: '' })
  const [copiado, setCopiado] = useState(false)

  useEffect(() => {
    api.get('/info/server').then(r => setServerInfo(r.data)).catch(() => {})
  }, [])

  const exemploOrigem = origens.find(o => o.ativo) || origens[0]
  const host = serverInfo.ftp_endpoint || '<IP-DO-SERVIDOR>'
  const user = exemploOrigem?.usuario_ftp || 'fwm_00001'
  const exemplo = `/tool fetch url="ftp://${user}:<SENHA>@${host}/firmware.npk" mode=ftp`

  function copiar() {
    navigator.clipboard.writeText(exemplo)
    setCopiado(true)
    setTimeout(() => setCopiado(false), 1500)
  }

  return (
    <div className="bg-slate-800/60 border border-slate-700 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-2">
        <Server size={16} className="text-sky-400" />
        <h3 className="font-semibold text-white text-sm">Exemplo de uso no device (Mikrotik)</h3>
      </div>
      <p className="text-xs text-slate-400 mb-2">
        Comando que o device executa pra baixar do mirror. Substitua o nome do arquivo + senha pela credencial real.
      </p>
      <div className="flex gap-2">
        <code className="flex-1 bg-slate-900 border border-slate-700 rounded px-2.5 py-1.5 text-xs text-emerald-300 font-mono overflow-x-auto whitespace-nowrap">
          {exemplo}
        </code>
        <button onClick={copiar}
          className="shrink-0 px-2.5 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-slate-200">
          {copiado ? <Check size={14} /> : <Copy size={14} />}
        </button>
      </div>
      {!serverInfo.ftp_endpoint && (
        <p className="text-[11px] text-amber-300 mt-2">
          ⚠ <code>FTP_MASQUERADE_ADDRESS</code> não configurado no .env — substitua <code>&lt;IP-DO-SERVIDOR&gt;</code> manualmente.
        </p>
      )}
    </div>
  )
}

// ────────────────────────── Componente principal ──────────────────────────
export default function Firmwares() {
  const me = getUser()
  const podeMutar = ['admin', 'admin_empresa', 'operador'].includes(me?.role)
  const podeCRUDOrigem = ['admin', 'admin_empresa'].includes(me?.role)

  const [aba, setAba] = useState('arquivos')   // 'arquivos' | 'origens'
  const [firmwares, setFirmwares] = useState([])
  const [orfaos, setOrfaos] = useState([])
  const [origens, setOrigens] = useState([])
  const [carregando, setCarregando] = useState(true)
  const [showUpload, setShowUpload] = useState(false)
  const [origemModal, setOrigemModal] = useState({ open: false, origem: null })
  const [credencial, setCredencial] = useState(null)

  async function carregar() {
    setCarregando(true)
    try {
      const [r1, r2, r3] = await Promise.all([
        api.get('/firmwares'),
        api.get('/firmwares/orfaos'),
        api.get('/firmware-origens'),
      ])
      setFirmwares(r1.data)
      setOrfaos(r2.data)
      setOrigens(r3.data)
    } catch (e) {
      console.error('Falha ao carregar firmwares', e)
    } finally {
      setCarregando(false)
    }
  }

  useEffect(() => { carregar() }, [])

  async function deletarFirmware(fw) {
    if (!confirm(`Apagar "${fw.nome}" (${fw.arquivo_nome})? O arquivo será removido do disco.`)) return
    try {
      await api.delete(`/firmwares/${fw.id}`)
      carregar()
    } catch (e) {
      alert('Erro ao apagar: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function deletarOrfao(nome) {
    if (!confirm(`Apagar o arquivo órfão "${nome}"? Esta ação não pode ser desfeita.`)) return
    try {
      await api.delete(`/firmwares/orfaos/${encodeURIComponent(nome)}`)
      carregar()
    } catch (e) {
      alert('Erro ao apagar órfão: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function deletarOrigem(o) {
    if (!confirm(`Excluir origem "${o.nome}" (${o.usuario_ftp})? Devices que usavam essa credencial perderão acesso imediatamente.`)) return
    try {
      await api.delete(`/firmware-origens/${o.id}`)
      carregar()
    } catch (e) {
      alert('Erro: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function toggleAtivo(o) {
    try {
      await api.patch(`/firmware-origens/${o.id}`, { ativo: !o.ativo })
      carregar()
    } catch (e) {
      alert('Erro: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function regerar(o) {
    if (!confirm(`Regenerar a senha de "${o.nome}"? Devices que usam a senha atual vão parar até serem atualizados.`)) return
    try {
      const r = await api.post(`/firmware-origens/${o.id}/regen-senha`)
      setCredencial(r.data)
      carregar()
    } catch (e) {
      alert('Erro ao regerar senha: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function verCredencial(o) {
    try {
      const r = await api.get(`/firmware-origens/${o.id}/credencial`)
      setCredencial(r.data)   // reabre o modal com user+senha pra copiar
    } catch (e) {
      alert('Erro ao buscar credencial: ' + (e.response?.data?.detail || e.message))
    }
  }

  async function promoverOrfao(o) {
    const nome = prompt(`Promover "${o.arquivo_nome}" a firmware catalogado.\n\nNome de exibição:`, o.arquivo_nome)
    if (!nome) return
    const fabricante = prompt('Fabricante (opcional):', '') || ''
    const versao = prompt('Versão (opcional):', '') || ''
    try {
      const fd = new FormData()
      fd.append('nome', nome.trim())
      if (fabricante.trim()) fd.append('fabricante', fabricante.trim())
      if (versao.trim()) fd.append('versao', versao.trim())
      await api.post(`/firmwares/orfaos/${encodeURIComponent(o.arquivo_nome)}/promover`, fd)
      carregar()
    } catch (e) {
      alert('Erro ao promover: ' + (e.response?.data?.detail || e.message))
    }
  }

  function downloadFirmware(fw) {
    // FileResponse do FastAPI — abre numa nova aba pra navegador disparar download
    const token = localStorage.getItem('token')
    // Browser não anexa Authorization em <a href> nem em window.open — buscamos
    // como blob via api (com interceptor JWT) e disparamos download programático.
    api.get(`/firmwares/${fw.id}/download`, { responseType: 'blob' })
      .then(r => {
        const url = window.URL.createObjectURL(r.data)
        const a = document.createElement('a')
        a.href = url
        a.download = fw.arquivo_nome
        document.body.appendChild(a)
        a.click()
        a.remove()
        window.URL.revokeObjectURL(url)
      })
      .catch(e => alert('Erro no download: ' + (e.response?.data?.detail || e.message)))
  }

  return (
    <div className="p-4 md:p-6 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <HardDrive size={24} /> Firmwares
          </h1>
          <p className="text-sm text-slate-400 mt-0.5">Mirror FTP de firmwares pra devices baixarem via /tool fetch ou equivalente.</p>
        </div>
        <button onClick={carregar}
          className="text-xs text-slate-400 hover:text-white flex items-center gap-1.5 px-2 py-1 rounded hover:bg-slate-700">
          <RefreshCw size={12} /> Atualizar
        </button>
      </div>

      {/* Tabs */}
      <div className="border-b border-slate-700 flex gap-1">
        <button onClick={() => setAba('arquivos')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            aba === 'arquivos'
              ? 'border-sky-500 text-sky-400'
              : 'border-transparent text-slate-400 hover:text-white'
          }`}>
          Arquivos <span className="ml-1 text-xs opacity-60">({firmwares.length}{orfaos.length > 0 && ` + ${orfaos.length} órfãos`})</span>
        </button>
        <button onClick={() => setAba('origens')}
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            aba === 'origens'
              ? 'border-sky-500 text-sky-400'
              : 'border-transparent text-slate-400 hover:text-white'
          }`}>
          Origens FTP <span className="ml-1 text-xs opacity-60">({origens.length})</span>
        </button>
      </div>

      {/* Card de exemplo (visível em ambas as abas) */}
      {origens.length > 0 && <ExemploComandoCard origens={origens} />}

      {carregando && (
        <div className="flex items-center justify-center py-12 text-slate-500">
          <Loader2 className="animate-spin mr-2" size={18} /> Carregando…
        </div>
      )}

      {/* ───── Aba: Arquivos ───── */}
      {!carregando && aba === 'arquivos' && (
        <div className="space-y-4">
          {podeMutar && (
            <div className="flex justify-end">
              <button onClick={() => setShowUpload(true)}
                className="px-3 py-1.5 bg-sky-500 hover:bg-sky-600 text-white rounded font-medium text-sm flex items-center gap-1.5">
                <Upload size={14} /> Upload de firmware
              </button>
            </div>
          )}

          {firmwares.length === 0 && orfaos.length === 0 && (
            <div className="text-center py-12 text-slate-500 border border-dashed border-slate-700 rounded-lg">
              <HardDrive size={36} className="mx-auto mb-2 opacity-50" />
              <p>Nenhum firmware cadastrado.</p>
              {podeMutar && <p className="text-xs mt-1">Clique em "Upload de firmware" pra começar.</p>}
            </div>
          )}

          {/* Firmwares catalogados */}
          {firmwares.length > 0 && (
            <div className="bg-slate-800/60 border border-slate-700 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-slate-900/60 text-xs uppercase text-slate-400">
                  <tr>
                    <th className="text-left px-3 py-2">Nome</th>
                    <th className="text-left px-3 py-2">Arquivo</th>
                    <th className="text-left px-3 py-2">Tamanho</th>
                    <th className="text-left px-3 py-2">Fabricante</th>
                    <th className="text-left px-3 py-2">Versão</th>
                    <th className="text-left px-3 py-2">Enviado</th>
                    <th className="text-right px-3 py-2 w-32">Ações</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {firmwares.map(fw => (
                    <tr key={fw.id} className="hover:bg-slate-700/30">
                      <td className="px-3 py-2 text-white">
                        <div className="font-medium">{fw.nome}</div>
                        {fw.descricao && <div className="text-xs text-slate-500 truncate max-w-xs">{fw.descricao}</div>}
                      </td>
                      <td className="px-3 py-2">
                        <code className="text-xs text-sky-300 font-mono">{fw.arquivo_nome}</code>
                        <div className="text-[10px] text-slate-500 font-mono" title={fw.sha256}>sha256 {fw.sha256.slice(0, 12)}…</div>
                      </td>
                      <td className="px-3 py-2 text-slate-300">{humanizarBytes(fw.tamanho_bytes)}</td>
                      <td className="px-3 py-2 text-slate-300">{fw.fabricante || '—'}{fw.modelo_alvo && <div className="text-xs text-slate-500">{fw.modelo_alvo}</div>}</td>
                      <td className="px-3 py-2 text-slate-300">{fw.versao || '—'}</td>
                      <td className="px-3 py-2 text-xs text-slate-400">
                        {formatarData(fw.criado_em)}
                        <div className="text-[10px] text-slate-500">{fw.criado_por_nome}</div>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <div className="inline-flex gap-1">
                          <button onClick={() => downloadFirmware(fw)}
                            title="Baixar pelo painel"
                            className="p-1.5 text-slate-400 hover:text-sky-400 hover:bg-slate-700 rounded">
                            <Download size={14} />
                          </button>
                          {podeMutar && (
                            <button onClick={() => deletarFirmware(fw)}
                              title="Apagar"
                              className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-slate-700 rounded">
                              <Trash2 size={14} />
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Órfãos */}
          {orfaos.length > 0 && (
            <div className="bg-amber-500/5 border border-amber-500/30 rounded-lg overflow-hidden">
              <div className="px-3 py-2 bg-amber-500/10 border-b border-amber-500/30 flex items-center gap-2">
                <FileQuestion size={16} className="text-amber-400" />
                <h3 className="font-medium text-amber-300 text-sm">Uploads externos (não catalogados)</h3>
                <span className="text-xs text-amber-300/70">— arquivos subidos via FTP por origens; promova ou delete</span>
              </div>
              <table className="w-full text-sm">
                <thead className="bg-slate-900/40 text-xs uppercase text-slate-400">
                  <tr>
                    <th className="text-left px-3 py-2">Arquivo</th>
                    <th className="text-left px-3 py-2">Tamanho</th>
                    <th className="text-left px-3 py-2">Modificado</th>
                    <th className="text-right px-3 py-2 w-32">Ações</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-amber-500/10">
                  {orfaos.map(o => (
                    <tr key={o.arquivo_nome} className="hover:bg-amber-500/5">
                      <td className="px-3 py-2">
                        <code className="text-sky-300 font-mono">{o.arquivo_nome}</code>
                      </td>
                      <td className="px-3 py-2 text-slate-300">{humanizarBytes(o.tamanho_bytes)}</td>
                      <td className="px-3 py-2 text-xs text-slate-400">{formatarData(o.modificado_em)}</td>
                      <td className="px-3 py-2 text-right">
                        {podeMutar && (
                          <div className="inline-flex gap-1">
                            <button onClick={() => promoverOrfao(o)}
                              title="Promover a firmware catalogado"
                              className="px-2 py-1 text-xs bg-sky-500/15 text-sky-300 hover:bg-sky-500/25 rounded">
                              Promover
                            </button>
                            <button onClick={() => deletarOrfao(o.arquivo_nome)}
                              title="Apagar do disco"
                              className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-slate-700 rounded">
                              <Trash2 size={14} />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ───── Aba: Origens FTP ───── */}
      {!carregando && aba === 'origens' && (
        <div className="space-y-4">
          {podeCRUDOrigem && (
            <div className="flex justify-end">
              <button onClick={() => setOrigemModal({ open: true, origem: null })}
                className="px-3 py-1.5 bg-sky-500 hover:bg-sky-600 text-white rounded font-medium text-sm flex items-center gap-1.5">
                <Plus size={14} /> Nova origem
              </button>
            </div>
          )}

          {origens.length === 0 && (
            <div className="text-center py-12 text-slate-500 border border-dashed border-slate-700 rounded-lg">
              <KeyRound size={36} className="mx-auto mb-2 opacity-50" />
              <p>Nenhuma origem FTP cadastrada.</p>
              {podeCRUDOrigem && <p className="text-xs mt-1">Cadastre origens pra que devices possam baixar firmwares via FTP.</p>}
            </div>
          )}

          {origens.length > 0 && (
            <div className="bg-slate-800/60 border border-slate-700 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-slate-900/60 text-xs uppercase text-slate-400">
                  <tr>
                    <th className="text-left px-3 py-2">Nome</th>
                    <th className="text-left px-3 py-2">Usuário FTP</th>
                    <th className="text-left px-3 py-2">Status</th>
                    <th className="text-left px-3 py-2">IP whitelist</th>
                    <th className="text-left px-3 py-2">Último acesso</th>
                    <th className="text-left px-3 py-2">Criado</th>
                    <th className="text-right px-3 py-2 w-56">Ações</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-700">
                  {origens.map(o => (
                    <tr key={o.id} className="hover:bg-slate-700/30">
                      <td className="px-3 py-2 text-white">
                        <div className="font-medium">{o.nome}</div>
                        {o.descricao && <div className="text-xs text-slate-500 truncate max-w-xs">{o.descricao}</div>}
                      </td>
                      <td className="px-3 py-2">
                        <code className="text-sky-300 font-mono">{o.usuario_ftp}</code>
                      </td>
                      <td className="px-3 py-2">
                        {o.ativo
                          ? <span className="text-xs bg-emerald-500/15 text-emerald-300 px-2 py-0.5 rounded">Ativo</span>
                          : <span className="text-xs bg-slate-700 text-slate-400 px-2 py-0.5 rounded">Inativo</span>}
                      </td>
                      <td className="px-3 py-2 text-xs">
                        {o.origem_cidr
                          ? <code className="text-amber-300 font-mono text-[11px]">{o.origem_cidr}</code>
                          : <span className="text-slate-600">qualquer IP</span>}
                      </td>
                      <td className="px-3 py-2 text-xs text-slate-400">
                        {o.ultimo_acesso_em ? formatarData(o.ultimo_acesso_em) : <span className="text-slate-600">nunca</span>}
                        {o.ultimo_ip && <div className="text-[10px] text-slate-500 font-mono">{o.ultimo_ip}</div>}
                      </td>
                      <td className="px-3 py-2 text-xs text-slate-400">
                        {formatarData(o.criado_em)}
                        <div className="text-[10px] text-slate-500">{o.criado_por_nome}</div>
                      </td>
                      <td className="px-3 py-2 text-right">
                        {podeCRUDOrigem && (
                          <div className="inline-flex gap-1">
                            <button onClick={() => verCredencial(o)}
                              title="Ver / copiar usuário e senha"
                              className="p-1.5 text-sky-400 hover:bg-slate-700 rounded">
                              <KeyRound size={14} />
                            </button>
                            <button onClick={() => setOrigemModal({ open: true, origem: o })}
                              title="Editar (nome, descrição, IPs)"
                              className="p-1.5 text-slate-300 hover:text-white hover:bg-slate-700 rounded">
                              <Edit3 size={14} />
                            </button>
                            <button onClick={() => toggleAtivo(o)}
                              title={o.ativo ? 'Desativar' : 'Ativar'}
                              className={`p-1.5 hover:bg-slate-700 rounded ${o.ativo ? 'text-emerald-400' : 'text-slate-500'}`}>
                              <Power size={14} />
                            </button>
                            <button onClick={() => regerar(o)}
                              title="Regerar senha"
                              className="p-1.5 text-amber-400 hover:bg-slate-700 rounded">
                              <RefreshCw size={14} />
                            </button>
                            <button onClick={() => deletarOrigem(o)}
                              title="Excluir"
                              className="p-1.5 text-slate-400 hover:text-red-400 hover:bg-slate-700 rounded">
                              <Trash2 size={14} />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Modals */}
      <UploadModal open={showUpload} onClose={() => setShowUpload(false)} onSucesso={carregar} />
      <OrigemModal
        open={origemModal.open}
        origem={origemModal.origem}
        onClose={() => setOrigemModal({ open: false, origem: null })}
        onSucesso={(cred) => { if (cred) setCredencial(cred); carregar() }}
      />
      <CredencialModal credencial={credencial} onClose={() => setCredencial(null)} />
    </div>
  )
}
