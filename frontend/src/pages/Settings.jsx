import { useEffect, useState } from 'react'
import { Clock, Save, ScrollText } from 'lucide-react'
import api from '../services/api'

// Converte dias <-> exibição em dias/meses (mês = 30 dias)
function daysToView(totalDays) {
  if (totalDays === 0) return { value: 0, unit: 'dias' }
  if (totalDays % 30 === 0 && totalDays >= 30) return { value: totalDays / 30, unit: 'meses' }
  return { value: totalDays, unit: 'dias' }
}
function viewToDays(value, unit) {
  const n = Math.max(0, Number(value) || 0)
  return unit === 'meses' ? n * 30 : n
}

export default function Settings() {
  const [hour, setHour] = useState(2)
  const [minute, setMinute] = useState(0)
  const [retValue, setRetValue] = useState(30)
  const [retUnit, setRetUnit] = useState('dias')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  useEffect(() => {
    api.get('/settings/schedule')
      .then(r => {
        setHour(r.data.backup_hour)
        setMinute(r.data.backup_minute)
        const v = daysToView(r.data.log_retention_days ?? 30)
        setRetValue(v.value)
        setRetUnit(v.unit)
      })
      .finally(() => setLoading(false))
  }, [])

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setMessage(null)
    try {
      await api.put('/settings/schedule', {
        backup_hour: hour,
        backup_minute: minute,
        log_retention_days: viewToDays(retValue, retUnit),
      })
      setMessage({ type: 'success', text: 'Configurações atualizadas com sucesso!' })
    } catch {
      setMessage({ type: 'error', text: 'Erro ao salvar. Verifique suas permissões.' })
    } finally {
      setSaving(false)
    }
  }

  const pad = v => String(v).padStart(2, '0')
  const totalDias = viewToDays(retValue, retUnit)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Configurações</h1>
        <p className="text-slate-400 text-sm mt-1">Ajustes gerais do sistema</p>
      </div>

      <form onSubmit={handleSave} className="space-y-6 max-w-lg">
        <div className="bg-slate-800 border border-slate-700 rounded-xl">
          <div className="p-5 border-b border-slate-700 flex items-center gap-2">
            <Clock size={18} className="text-slate-400" />
            <h2 className="font-semibold text-white">Horário do Backup Automático</h2>
          </div>

          <div className="p-5 space-y-5">
            {loading ? (
              <p className="text-slate-400 text-sm">Carregando...</p>
            ) : (
              <>
                <div className="flex items-center gap-4">
                  <div className="flex-1">
                    <label className="block text-sm text-slate-400 mb-1">Hora</label>
                    <select value={hour} onChange={e => setHour(Number(e.target.value))}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500">
                      {Array.from({ length: 24 }, (_, i) => (
                        <option key={i} value={i}>{pad(i)}h</option>
                      ))}
                    </select>
                  </div>
                  <div className="flex-1">
                    <label className="block text-sm text-slate-400 mb-1">Minuto</label>
                    <select value={minute} onChange={e => setMinute(Number(e.target.value))}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500">
                      {[0, 15, 30, 45].map(m => (
                        <option key={m} value={m}>{pad(m)}min</option>
                      ))}
                    </select>
                  </div>
                </div>
                <p className="text-slate-500 text-xs">
                  Backup diário agendado para <span className="text-sky-400 font-medium">{pad(hour)}:{pad(minute)}</span>.
                </p>
              </>
            )}
          </div>
        </div>

        <div className="bg-slate-800 border border-slate-700 rounded-xl">
          <div className="p-5 border-b border-slate-700 flex items-center gap-2">
            <ScrollText size={18} className="text-slate-400" />
            <h2 className="font-semibold text-white">Retenção de Logs do Scheduler</h2>
          </div>

          <div className="p-5 space-y-4">
            {loading ? (
              <p className="text-slate-400 text-sm">Carregando...</p>
            ) : (
              <>
                <div className="flex items-end gap-3">
                  <div className="flex-1">
                    <label className="block text-sm text-slate-400 mb-1">Apagar logs com mais de</label>
                    <input type="number" min="0" max="3650" value={retValue}
                      onChange={e => setRetValue(e.target.value)}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500" />
                  </div>
                  <div className="flex-1">
                    <label className="block text-sm text-slate-400 mb-1">Unidade</label>
                    <select value={retUnit} onChange={e => setRetUnit(e.target.value)}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500">
                      <option value="dias">Dias</option>
                      <option value="meses">Meses</option>
                    </select>
                  </div>
                </div>
                <p className="text-slate-500 text-xs">
                  {totalDias === 0
                    ? <>Purga automática <span className="text-amber-400 font-medium">desativada</span> (os logs ficam indefinidamente).</>
                    : <>Purga automática ativa — logs com mais de <span className="text-sky-400 font-medium">{totalDias} dias</span> são removidos diariamente às 03:00 UTC.</>
                  }
                </p>
              </>
            )}
          </div>
        </div>

        {message && (
          <p className={`text-sm ${message.type === 'success' ? 'text-emerald-400' : 'text-red-400'}`}>
            {message.text}
          </p>
        )}

        <button type="submit" disabled={saving || loading}
          className="flex items-center gap-2 px-4 py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors">
          <Save size={16} />
          {saving ? 'Salvando...' : 'Salvar configurações'}
        </button>
      </form>
    </div>
  )
}
