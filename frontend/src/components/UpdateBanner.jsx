import { useEffect, useState } from 'react'
import { Sparkles, X, ShieldCheck, FlaskConical } from 'lucide-react'
import api from '../services/api'

// Banner de "versão nova disponível". Aparece no topo de todas as páginas
// quando o backend acha update no canal escolhido (LTS/Edge — v2.3.0).
// Update propriamente dito é manual via SSH; banner só notifica. Detalhe
// completo (canal, datas, comando SSH copy) fica na seção "Atualização do
// sistema" em Configurações.

const DISMISSED_KEY = 'nexusUpdateBannerDismissedTag'
const POLL_INTERVAL_MS = 30 * 60 * 1000  // 30min — alinha com TTL 1h do cache backend

export default function UpdateBanner() {
  const [info, setInfo] = useState(null)
  const [showModal, setShowModal] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  async function check() {
    try {
      const { data } = await api.get('/version/check')
      setInfo(data)
      // Banner volta automaticamente quando chega versão nova: dispensar uma
      // tag não dispensa as próximas. localStorage guarda só a tag dispensada.
      const dismissedTag = localStorage.getItem(DISMISSED_KEY)
      setDismissed(Boolean(data.update_available && dismissedTag === data.latest))
    } catch {
      // Silencioso. Endpoint pode falhar (token errado, GitHub fora, etc.) —
      // sem dados = sem banner.
    }
  }

  useEffect(() => {
    check()
    const id = setInterval(check, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [])

  function dismiss() {
    if (info?.latest) localStorage.setItem(DISMISSED_KEY, info.latest)
    setDismissed(true)
  }

  if (!info?.update_available || dismissed) return null

  // Canal do release alvo — se backend mandar `target.is_lts`, usa. Senão
  // cai no canal da instância (compat com versões antigas do backend).
  const isLts = info.target ? info.target.is_lts : info.channel !== 'edge'
  const CanalIcon = isLts ? ShieldCheck : FlaskConical
  const canalLabel = isLts ? 'LTS' : 'Edge'
  const canalCls = isLts ? 'text-emerald-300' : 'text-amber-300'

  return (
    <>
      <div className="bg-sky-500/10 border-b border-sky-500/30 px-4 py-2 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-sky-100 min-w-0">
          <Sparkles size={15} className="text-sky-400 shrink-0" />
          <span className="truncate">
            <strong className="text-sky-200">Versão {info.latest} disponível</strong>
            <span className={`ml-2 text-xs inline-flex items-center gap-1 ${canalCls}`}>
              <CanalIcon size={11} /> {canalLabel}
            </span>
            <span className="text-slate-400 ml-2 text-xs">você está em v{info.current}</span>
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {info.changelog_summary && (
            <button onClick={() => setShowModal(true)}
              className="text-xs px-3 py-1 bg-sky-500/20 hover:bg-sky-500/30 text-sky-300 border border-sky-500/40 rounded transition-colors">
              Ver mudanças
            </button>
          )}
          <button onClick={dismiss} title="Dispensar (volta na próxima versão)"
            className="text-slate-400 hover:text-white p-1">
            <X size={15} />
          </button>
        </div>
      </div>

      {showModal && info.changelog_summary && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="bg-slate-800 rounded-2xl border border-slate-700 w-full max-w-2xl max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between p-4 border-b border-slate-700">
              <div className="min-w-0 pr-3">
                <h2 className="font-semibold text-white">O que muda em {info.latest}</h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Você está em v{info.current}. Comando SSH pronto pra copiar fica em
                  <span className="text-slate-300"> Configurações → Atualização do sistema</span>.
                </p>
              </div>
              <button onClick={() => setShowModal(false)} className="text-slate-400 hover:text-white shrink-0">
                <X size={18} />
              </button>
            </div>
            <div className="flex-1 overflow-auto p-5">
              <pre className="text-xs text-slate-300 whitespace-pre-wrap font-mono leading-relaxed">
                {info.changelog_summary}
              </pre>
            </div>
            <div className="p-4 border-t border-slate-700 flex justify-end gap-2">
              <button onClick={() => setShowModal(false)}
                className="bg-slate-700 hover:bg-slate-600 text-white px-4 py-2 rounded-lg text-sm transition-colors">
                Fechar
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
