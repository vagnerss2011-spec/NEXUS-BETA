import { useMemo, useState } from 'react'
import {
  Sparkles, Wifi, Clock, Trash2, Terminal, Image, BellRing,
  ScrollText, Upload, Bell, Wrench, Bug, Megaphone,
} from 'lucide-react'
import { CHANGELOG_ENTRIES } from '../data/changelog'

// Map nome-string -> componente lucide-react.
// Fica aqui (não no data/) pra não acoplar o arquivo de dados a JSX/imports.
const ICONES = {
  Sparkles, Wifi, Clock, Trash2, Terminal, Image, BellRing,
  ScrollText, Upload, Bell,
}

// Configuração visual por tipo. label aparece no chip e no badge da entrada;
// cls é classe Tailwind do badge (mesma família usada em outras páginas).
const TIPO_CONFIG = {
  novidade:  { label: 'Novidade',  Icon: Sparkles, cls: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300' },
  melhoria:  { label: 'Melhoria',  Icon: Wrench,   cls: 'bg-sky-500/10 border-sky-500/30 text-sky-300' },
  correcao:  { label: 'Correção', Icon: Bug,      cls: 'bg-amber-500/10 border-amber-500/30 text-amber-300' },
}

const FILTROS = [
  { key: 'all',      label: 'Tudo' },
  { key: 'novidade', label: 'Novidades' },
  { key: 'melhoria', label: 'Melhorias' },
  { key: 'correcao', label: 'Correções' },
]

function formatarData(iso) {
  // ISO "AAAA-MM-DD" -> "DD/MM/AAAA" — evita ambiguidade pt-BR vs en-US sem
  // depender de toLocaleDateString (que varia conforme locale do browser).
  const [a, m, d] = iso.split('-')
  return `${d}/${m}/${a}`
}

export default function Novidades() {
  const [filtro, setFiltro] = useState('all')

  const entradas = useMemo(() => {
    const lista = filtro === 'all'
      ? CHANGELOG_ENTRIES
      : CHANGELOG_ENTRIES.filter(e => e.tipo === filtro)
    // ordena mais recente primeiro (a data já está em ISO, ordenação string funciona)
    return [...lista].sort((a, b) => (a.data < b.data ? 1 : -1))
  }, [filtro])

  // Contadores por tipo, pra mostrar no chip de filtro
  const counts = useMemo(() => {
    const c = { all: CHANGELOG_ENTRIES.length, novidade: 0, melhoria: 0, correcao: 0 }
    for (const e of CHANGELOG_ENTRIES) c[e.tipo] = (c[e.tipo] || 0) + 1
    return c
  }, [])

  return (
    <div className="p-4 sm:p-6 lg:p-8 max-w-4xl mx-auto">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-10 h-10 rounded-lg bg-sky-500/15 border border-sky-500/30 flex items-center justify-center">
            <Megaphone size={20} className="text-sky-400" />
          </div>
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold text-white">Novidades</h1>
            <p className="text-sm text-slate-400">O que mudou no NEXUS BACKUP nas últimas atualizações</p>
          </div>
        </div>
      </div>

      {/* Filtros */}
      <div className="flex flex-wrap gap-2 mb-6">
        {FILTROS.map(f => {
          const ativo = filtro === f.key
          return (
            <button
              key={f.key}
              onClick={() => setFiltro(f.key)}
              className={`px-3 py-1.5 rounded-full text-sm border transition-colors ${
                ativo
                  ? 'bg-sky-500/20 border-sky-500/50 text-sky-300'
                  : 'bg-slate-800 border-slate-700 text-slate-400 hover:text-white hover:border-slate-600'
              }`}
            >
              {f.label}
              <span className={`ml-1.5 text-xs ${ativo ? 'text-sky-400' : 'text-slate-500'}`}>
                {counts[f.key] ?? 0}
              </span>
            </button>
          )
        })}
      </div>

      {/* Lista */}
      {entradas.length === 0 ? (
        <div className="bg-slate-800 border border-slate-700 rounded-lg p-8 text-center">
          <p className="text-slate-400 text-sm">Nenhuma entrada para este filtro.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {entradas.map((e, idx) => {
            const tipoCfg = TIPO_CONFIG[e.tipo] || TIPO_CONFIG.melhoria
            // ícone da entrada: usa o nome em e.icone se mapeado, senão cai no do tipo
            const EntradaIcon = ICONES[e.icone] || tipoCfg.Icon

            return (
              <article
                key={`${e.data}-${idx}`}
                className="bg-slate-800 border border-slate-700 rounded-lg p-4 sm:p-5 hover:border-slate-600 transition-colors"
              >
                <div className="flex items-start gap-3 sm:gap-4">
                  <div className="shrink-0 w-10 h-10 rounded-lg bg-slate-900 border border-slate-700 flex items-center justify-center">
                    <EntradaIcon size={18} className="text-sky-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-1.5">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[11px] font-medium ${tipoCfg.cls}`}>
                        <tipoCfg.Icon size={11} />
                        {tipoCfg.label}
                      </span>
                      {e.categoria && (
                        <span className="text-[11px] text-slate-500 bg-slate-900 border border-slate-700 px-2 py-0.5 rounded-full">
                          {e.categoria}
                        </span>
                      )}
                      <span className="text-[11px] text-slate-500 ml-auto">{formatarData(e.data)}</span>
                    </div>
                    <h2 className="text-base sm:text-lg font-semibold text-white mb-1.5 leading-snug">
                      {e.titulo}
                    </h2>
                    <p className="text-sm text-slate-400 leading-relaxed">
                      {e.descricao}
                    </p>
                  </div>
                </div>
              </article>
            )
          })}
        </div>
      )}

      {/* Rodapé sutil */}
      <p className="text-center text-[11px] text-slate-600 mt-8">
        As alterações técnicas detalhadas ficam no <span className="text-slate-500">CHANGELOG.md</span> do repositório.
      </p>
    </div>
  )
}
