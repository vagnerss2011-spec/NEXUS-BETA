import { useEffect, useState } from 'react'
import { Clock, Save, ScrollText, Bell, Send, AlertTriangle, Gauge, Cpu, ShieldCheck, Download, Cloud, Eye, EyeOff, CheckCircle, XCircle } from 'lucide-react'
import api from '../services/api'
import UpdateCard from '../components/UpdateCard'

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
  const user = JSON.parse(localStorage.getItem('user') || '{}')
  const isMaster = user.role === 'admin'

  const [hour, setHour] = useState(2)
  const [minute, setMinute] = useState(0)
  const [retValue, setRetValue] = useState(30)
  const [retUnit, setRetUnit] = useState('dias')
  // Tuning do scheduler diário (delay adaptativo entre devices)
  const [delayMin, setDelayMin] = useState(10)
  const [delayFator, setDelayFator] = useState(0.2)
  const [picoFator, setPicoFator] = useState(3.0)
  // Paralelismo adaptativo (Zabbix-like)
  const [workersMaxApi, setWorkersMaxApi] = useState(4)
  const [workersMaxSsh, setWorkersMaxSsh] = useState(2)
  const [cpuLimite, setCpuLimite] = useState(80)
  const [memLimite, setMemLimite] = useState(80)
  const [workersAuto, setWorkersAuto] = useState(true)

  // ===== Export do banco em .nxbak (admin master) — v2.0.0 =====
  const [dbExport, setDbExport] = useState(null)  // null = não carregado ainda
  const [dbExportSaving, setDbExportSaving] = useState(false)
  const [dbExportMessage, setDbExportMessage] = useState(null)
  const [dbExportSenha, setDbExportSenha] = useState('')  // '' = não tocar; 'apagar' = limpar; outro = setar
  const [mostrarSenhaExport, setMostrarSenhaExport] = useState(false)
  const [executandoExport, setExecutandoExport] = useState(false)
  const [executandoUpload, setExecutandoUpload] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)

  // ===== Telegram (admin master) =====
  const [tgConfigurado, setTgConfigurado] = useState(false)
  const [tgToken, setTgToken] = useState('')          // string vazia = não tocar; 'apagar' = limpar
  const [tgChatId, setTgChatId] = useState('')
  const [tgAlertaFalha, setTgAlertaFalha] = useState(true)
  const [tgAlertaPushNegado, setTgAlertaPushNegado] = useState(true)
  const [tgAlertaVolume, setTgAlertaVolume] = useState(true)
  const [tgSaving, setTgSaving] = useState(false)
  const [tgTestando, setTgTestando] = useState(false)
  const [tgMessage, setTgMessage] = useState(null)

  useEffect(() => {
    api.get('/settings/schedule')
      .then(r => {
        setHour(r.data.backup_hour)
        setMinute(r.data.backup_minute)
        const v = daysToView(r.data.log_retention_days ?? 30)
        setRetValue(v.value)
        setRetUnit(v.unit)
        setDelayMin(r.data.backup_delay_min_seg ?? 10)
        setDelayFator(r.data.backup_delay_fator ?? 0.2)
        setPicoFator(r.data.backup_pico_fator_critico ?? 3.0)
        setWorkersMaxApi(r.data.backup_workers_max_api ?? 4)
        setWorkersMaxSsh(r.data.backup_workers_max_ssh ?? 2)
        setCpuLimite(r.data.backup_cpu_limite_pct ?? 80)
        setMemLimite(r.data.backup_mem_limite_pct ?? 80)
        setWorkersAuto(r.data.backup_workers_auto ?? true)
      })
      .finally(() => setLoading(false))

    if (isMaster) {
      api.get('/settings/telegram')
        .then(r => {
          setTgConfigurado(r.data.bot_configurado)
          setTgChatId(r.data.chat_id_default || '')
          setTgAlertaFalha(r.data.alerta_falha_backup)
          setTgAlertaPushNegado(r.data.alerta_push_negado)
          setTgAlertaVolume(r.data.alerta_volume_alto)
        })
        .catch(() => {})  // se falhar (sem permissão), só não mostra

      // Export do banco — também só admin master
      api.get('/settings/db-export')
        .then(r => setDbExport(r.data))
        .catch(() => {})
    }
  }, [isMaster])

  // ===== Funções db-export =====
  function dbExportPatch(patch) {
    setDbExport(prev => ({ ...prev, ...patch }))
  }

  async function salvarDbExport() {
    setDbExportSaving(true)
    setDbExportMessage(null)
    try {
      const payload = {
        db_export_enabled: dbExport.db_export_enabled,
        db_export_hour: Number(dbExport.db_export_hour),
        db_export_minute: Number(dbExport.db_export_minute),
        db_export_remote_enabled: dbExport.db_export_remote_enabled,
        db_export_remote_protocolo: dbExport.db_export_remote_protocolo,
        db_export_remote_host: dbExport.db_export_remote_host || null,
        db_export_remote_porta: Number(dbExport.db_export_remote_porta),
        db_export_remote_user: dbExport.db_export_remote_user || null,
        db_export_remote_path: dbExport.db_export_remote_path || '/',
        db_export_remote_dia_semana: Number(dbExport.db_export_remote_dia_semana),
        db_export_remote_hora: Number(dbExport.db_export_remote_hora),
        db_export_remote_minute: Number(dbExport.db_export_remote_minute),
      }
      // Senha: '' = não tocar; 'apagar' = limpar; outro = setar
      if (dbExportSenha === 'apagar') payload.db_export_remote_senha = ''
      else if (dbExportSenha && dbExportSenha.length > 0) payload.db_export_remote_senha = dbExportSenha
      const { data } = await api.put('/settings/db-export', payload)
      setDbExport(data)
      setDbExportSenha('')
      setDbExportMessage({ type: 'success', text: 'Configuração de export salva.' })
    } catch (err) {
      setDbExportMessage({ type: 'error', text: err?.response?.data?.detail || 'Erro ao salvar.' })
    } finally {
      setDbExportSaving(false)
    }
  }

  async function executarExportAgora() {
    setExecutandoExport(true)
    setDbExportMessage(null)
    try {
      const { data } = await api.post('/settings/db-export/run-now')
      setDbExportMessage({ type: 'success', text: `Export gerado: ${data.arquivo} (${(data.tamanho_bytes / 1024).toFixed(1)} KB)` })
      const r = await api.get('/settings/db-export')
      setDbExport(r.data)
    } catch (err) {
      setDbExportMessage({ type: 'error', text: err?.response?.data?.detail || 'Falha ao exportar.' })
    } finally {
      setExecutandoExport(false)
    }
  }

  async function executarUploadAgora() {
    setExecutandoUpload(true)
    setDbExportMessage(null)
    try {
      const { data } = await api.post('/settings/db-export/upload-now')
      setDbExportMessage({
        type: data.ok ? 'success' : 'error',
        text: data.mensagem || (data.ok ? 'Upload enviado.' : 'Falha no upload.'),
      })
    } catch (err) {
      setDbExportMessage({ type: 'error', text: err?.response?.data?.detail || 'Erro no upload.' })
    } finally {
      setExecutandoUpload(false)
    }
  }

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setMessage(null)
    try {
      await api.put('/settings/schedule', {
        backup_hour: hour,
        backup_minute: minute,
        log_retention_days: viewToDays(retValue, retUnit),
        backup_delay_min_seg: Number(delayMin),
        backup_delay_fator: Number(delayFator),
        backup_pico_fator_critico: Number(picoFator),
        backup_workers_max_api: Number(workersMaxApi),
        backup_workers_max_ssh: Number(workersMaxSsh),
        backup_cpu_limite_pct: Number(cpuLimite),
        backup_mem_limite_pct: Number(memLimite),
        backup_workers_auto: !!workersAuto,
      })
      setMessage({ type: 'success', text: 'Configurações atualizadas com sucesso!' })
    } catch {
      setMessage({ type: 'error', text: 'Erro ao salvar. Verifique suas permissões.' })
    } finally {
      setSaving(false)
    }
  }

  async function salvarTelegram() {
    setTgSaving(true)
    setTgMessage(null)
    try {
      // bot_token: vazio = não tocar; 'apagar' = limpar; outro = setar
      const payload = {
        chat_id_default: tgChatId,
        alerta_falha_backup: tgAlertaFalha,
        alerta_push_negado: tgAlertaPushNegado,
        alerta_volume_alto: tgAlertaVolume,
      }
      if (tgToken === 'apagar') {
        payload.bot_token = ''  // backend interpreta como "limpar"
      } else if (tgToken && tgToken.length > 5) {
        payload.bot_token = tgToken
      }
      const { data } = await api.put('/settings/telegram', payload)
      setTgConfigurado(data.bot_configurado)
      setTgChatId(data.chat_id_default || '')
      setTgAlertaFalha(data.alerta_falha_backup)
      setTgAlertaPushNegado(data.alerta_push_negado)
      setTgAlertaVolume(data.alerta_volume_alto)
      setTgToken('')  // não persistir o token na UI após salvar
      setTgMessage({ type: 'success', text: 'Configuração Telegram salva.' })
    } catch (err) {
      const detail = err?.response?.data?.detail || 'Erro ao salvar Telegram.'
      setTgMessage({ type: 'error', text: detail })
    } finally {
      setTgSaving(false)
    }
  }

  async function testarTelegram() {
    setTgTestando(true)
    setTgMessage(null)
    try {
      const { data } = await api.post('/settings/telegram/test', {})
      setTgMessage({
        type: data.ok ? 'success' : 'error',
        text: data.mensagem || (data.ok ? 'Teste enviado.' : 'Falha no teste.'),
      })
    } catch (err) {
      setTgMessage({ type: 'error', text: err?.response?.data?.detail || 'Erro ao testar.' })
    } finally {
      setTgTestando(false)
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
          <div className="p-4 sm:p-5 border-b border-slate-700 flex items-center gap-2">
            <Clock size={18} className="text-slate-400" />
            <h2 className="font-semibold text-white">Horário do Backup Automático</h2>
          </div>

          <div className="p-4 sm:p-5 space-y-5">
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
          <div className="p-4 sm:p-5 border-b border-slate-700 flex items-center gap-2">
            <Gauge size={18} className="text-slate-400" />
            <h2 className="font-semibold text-white">Ritmo do Scheduler (delay adaptativo)</h2>
          </div>
          <div className="p-4 sm:p-5 space-y-4">
            {loading ? (
              <p className="text-slate-400 text-sm">Carregando...</p>
            ) : (
              <>
                <p className="text-xs text-slate-500">
                  Controla o intervalo entre coletas no backup diário (apenas SSH/Telnet/API — push não é afetado).
                  Fórmula: <code className="text-sky-300">delay = max(mínimo, fator × duração_anterior)</code>.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Delay mínimo (s)</label>
                    <input type="number" min="0" max="600" value={delayMin}
                      onChange={e => setDelayMin(e.target.value)}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500" />
                    <p className="text-[11px] text-slate-500 mt-1">Piso entre devices. 0 = sem pausa.</p>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Fator adaptativo</label>
                    <input type="number" min="0" max="10" step="0.1" value={delayFator}
                      onChange={e => setDelayFator(e.target.value)}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500" />
                    <p className="text-[11px] text-slate-500 mt-1">0.2 = backup de 5min vira 60s de pausa.</p>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Pico crítico (×)</label>
                    <input type="number" min="1" max="100" step="0.1" value={picoFator}
                      onChange={e => setPicoFator(e.target.value)}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500" />
                    <p className="text-[11px] text-slate-500 mt-1">Multiplicador da média histórica que dispara alerta.</p>
                  </div>
                </div>
                <p className="text-slate-500 text-xs">
                  Com <span className="text-sky-400 font-medium">{delayMin}s</span> de piso e fator{' '}
                  <span className="text-sky-400 font-medium">{delayFator}</span>, devices que levam ~30s ficam com pausa de{' '}
                  <span className="text-sky-300 font-medium">{Math.max(Number(delayMin) || 0, Math.round((Number(delayFator) || 0) * 30))}s</span>{' '}
                  entre coletas. Picos &gt; <span className="text-amber-400 font-medium">{picoFator}×</span> a média do device viram alerta Telegram.
                </p>
              </>
            )}
          </div>
        </div>

        <div className="bg-slate-800 border border-slate-700 rounded-xl">
          <div className="p-4 sm:p-5 border-b border-slate-700 flex items-center gap-2">
            <Cpu size={18} className="text-slate-400" />
            <h2 className="font-semibold text-white">Paralelismo Adaptativo</h2>
          </div>
          <div className="p-4 sm:p-5 space-y-4">
            {loading ? (
              <p className="text-slate-400 text-sm">Carregando...</p>
            ) : (
              <>
                <p className="text-xs text-slate-500">
                  Workers paralelos auto-ajustáveis (estilo "Zabbix-agent") — sobem gradualmente e cortam pela metade se a infra
                  ficar sob stress. Pool API é separado de SSH/Telnet (paramiko consome mais recurso).
                </p>
                <label className="flex items-center gap-2 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    checked={!!workersAuto}
                    onChange={e => setWorkersAuto(e.target.checked)}
                    className="accent-sky-500"
                  />
                  <span className="text-slate-300">Auto-ajuste ligado</span>
                  <span className="text-xs text-slate-500">— desligado trava em 1 worker (modo legado sequencial)</span>
                </label>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Workers máx — API</label>
                    <input type="number" min="1" max="8" value={workersMaxApi}
                      onChange={e => setWorkersMaxApi(e.target.value)}
                      disabled={!workersAuto}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:opacity-50" />
                    <p className="text-[11px] text-slate-500 mt-1">RouterOS API binária — leve. Sugestão: 4</p>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Workers máx — SSH/Telnet</label>
                    <input type="number" min="1" max="8" value={workersMaxSsh}
                      onChange={e => setWorkersMaxSsh(e.target.value)}
                      disabled={!workersAuto}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:opacity-50" />
                    <p className="text-[11px] text-slate-500 mt-1">Paramiko/Netmiko consome mais. Sugestão: 2</p>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Limite CPU (%)</label>
                    <input type="number" min="30" max="95" value={cpuLimite}
                      onChange={e => setCpuLimite(e.target.value)}
                      disabled={!workersAuto}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:opacity-50" />
                    <p className="text-[11px] text-slate-500 mt-1">Média 60s — acima disso, corta workers pela metade.</p>
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-1">Limite RAM (%)</label>
                    <input type="number" min="30" max="95" value={memLimite}
                      onChange={e => setMemLimite(e.target.value)}
                      disabled={!workersAuto}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:opacity-50" />
                    <p className="text-[11px] text-slate-500 mt-1">Mesma regra do CPU — proteção contra OOM.</p>
                  </div>
                </div>
                <p className="text-slate-500 text-xs">
                  {workersAuto
                    ? <>Cap configurado: <span className="text-sky-400 font-medium">{workersMaxApi}</span> em API e{' '}
                       <span className="text-sky-400 font-medium">{workersMaxSsh}</span> em SSH/Telnet em paralelo.{' '}
                       Stress detectado em CPU &gt; <span className="text-amber-400 font-medium">{cpuLimite}%</span> ou RAM &gt;{' '}
                       <span className="text-amber-400 font-medium">{memLimite}%</span>.</>
                    : <span className="text-amber-400">Auto-ajuste desligado — sempre 1 worker (sequencial igual ao modo antigo).</span>
                  }
                </p>
              </>
            )}
          </div>
        </div>

        <div className="bg-slate-800 border border-slate-700 rounded-xl">
          <div className="p-4 sm:p-5 border-b border-slate-700 flex items-center gap-2">
            <ScrollText size={18} className="text-slate-400" />
            <h2 className="font-semibold text-white">Retenção de Logs do Scheduler</h2>
          </div>

          <div className="p-4 sm:p-5 space-y-4">
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

      {/* ===== Telegram (somente admin master) ===== */}
      {isMaster && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl max-w-lg">
          <div className="p-4 sm:p-5 border-b border-slate-700 flex items-center gap-2">
            <Bell size={18} className="text-slate-400" />
            <h2 className="font-semibold text-white">Notificações Telegram</h2>
          </div>
          <div className="p-4 sm:p-5 space-y-4">
            <div className="bg-sky-500/5 border border-sky-500/20 rounded-lg p-3 text-xs text-slate-300">
              <p>
                Bot único pra toda instalação. Cada empresa pode ter seu chat_id próprio
                (configurável na página <span className="text-sky-300">Empresas</span>) — caso vazio, usa o default abaixo.
              </p>
              <p className="mt-1.5 text-slate-400">
                <strong>Setup:</strong> crie bot via <span className="text-sky-300">@BotFather</span>,
                adicione ao grupo, e use <span className="text-sky-300">@RawDataBot</span> pra descobrir o chat_id (formato negativo).
              </p>
            </div>

            <div>
              <label className="block text-sm text-slate-400 mb-1">
                Token do bot
                {tgConfigurado && <span className="ml-2 text-emerald-400 text-xs">● configurado</span>}
              </label>
              <input
                type="password"
                value={tgToken}
                onChange={e => setTgToken(e.target.value)}
                placeholder={tgConfigurado ? '•••••••• (deixe vazio pra manter)' : '123456789:ABCdef...'}
                className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-sky-500"
              />
              {tgConfigurado && (
                <button
                  type="button"
                  onClick={() => setTgToken('apagar')}
                  className={`mt-1.5 text-xs ${tgToken === 'apagar' ? 'text-red-400' : 'text-slate-500 hover:text-red-400'} transition-colors`}
                >
                  {tgToken === 'apagar' ? '⚠ token será removido ao salvar' : 'Remover token (desabilita Telegram)'}
                </button>
              )}
            </div>

            <div>
              <label className="block text-sm text-slate-400 mb-1">Chat ID default (grupo global)</label>
              <input
                type="text"
                value={tgChatId}
                onChange={e => setTgChatId(e.target.value)}
                placeholder="-1001234567890"
                className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-sky-500"
              />
              <p className="text-xs text-slate-500 mt-1">
                Empresas sem chat_id próprio recebem alertas aqui.
              </p>
            </div>

            <div className="space-y-2">
              <p className="text-sm text-slate-400">Categorias de alerta</p>
              {[
                { val: tgAlertaFalha, set: setTgAlertaFalha, label: 'Falha de backup SSH/Telnet (scheduler)' },
                { val: tgAlertaPushNegado, set: setTgAlertaPushNegado, label: 'IP fora da whitelist em push (FTP/SFTP/TFTP)' },
                { val: tgAlertaVolume, set: setTgAlertaVolume, label: 'Volume alto de uploads (>5/24h por device)' },
              ].map(({ val, set, label }) => (
                <label key={label} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={val}
                    onChange={e => set(e.target.checked)}
                    className="w-4 h-4 rounded border-slate-500 bg-slate-700 text-sky-500 focus:ring-sky-500"
                  />
                  {label}
                </label>
              ))}
            </div>

            {tgMessage && (
              <p className={`text-sm flex items-start gap-2 ${tgMessage.type === 'success' ? 'text-emerald-400' : 'text-red-400'}`}>
                {tgMessage.type !== 'success' && <AlertTriangle size={14} className="mt-0.5 shrink-0" />}
                {tgMessage.text}
              </p>
            )}

            <div className="flex flex-col sm:flex-row gap-2">
              <button
                type="button"
                onClick={salvarTelegram}
                disabled={tgSaving}
                className="flex items-center justify-center gap-2 px-4 py-2 bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors flex-1"
              >
                <Save size={16} />
                {tgSaving ? 'Salvando...' : 'Salvar'}
              </button>
              <button
                type="button"
                onClick={testarTelegram}
                disabled={tgTestando || !tgConfigurado}
                title={!tgConfigurado ? 'Salve o token primeiro' : 'Envia uma mensagem de teste pro chat default'}
                className="flex items-center justify-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors flex-1"
              >
                <Send size={16} />
                {tgTestando ? 'Enviando...' : 'Enviar teste'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ===== Export do Banco (.nxbak) — só admin master ===== */}
      {isMaster && dbExport && (
        <div className="bg-slate-800 border border-slate-700 rounded-xl max-w-3xl">
          <div className="p-4 sm:p-5 border-b border-slate-700 flex items-center gap-2">
            <ShieldCheck size={18} className="text-slate-400" />
            <h2 className="font-semibold text-white">Export do Banco (.nxbak)</h2>
            <span className="text-xs text-amber-400 ml-auto">v2.0.0</span>
          </div>
          <div className="p-4 sm:p-5 space-y-5">
            <p className="text-xs text-slate-500">
              Backup criptografado de todos os backups armazenados — pra recuperar mesmo se o servidor queimar.
              Gerado diariamente em formato proprietário, aberto só pela ferramenta externa que conhece o formato + a chave Fernet (<code className="text-slate-400">DB_EXPORT_KEY</code> no <code>.env</code>).
            </p>

            {/* Status da chave */}
            <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm ${
              dbExport.chave_configurada
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                : 'bg-amber-500/10 border-amber-500/30 text-amber-300'
            }`}>
              {dbExport.chave_configurada ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
              <span>
                {dbExport.chave_configurada
                  ? <>Chave de criptografia configurada — export funcional.</>
                  : <><b>DB_EXPORT_KEY não configurada no .env</b> — export desabilitado até admin gerar e setar a chave.</>}
              </span>
            </div>

            {/* Export local diário */}
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer text-sm">
                <input
                  type="checkbox"
                  checked={!!dbExport.db_export_enabled}
                  onChange={e => dbExportPatch({ db_export_enabled: e.target.checked })}
                  className="accent-sky-500"
                />
                <span className="text-slate-300">Export diário local ligado</span>
              </label>
              <div className="flex items-center gap-3 ml-6">
                <span className="text-xs text-slate-400">Horário:</span>
                <select value={dbExport.db_export_hour}
                  onChange={e => dbExportPatch({ db_export_hour: Number(e.target.value) })}
                  className="bg-slate-700 border border-slate-600 text-white rounded-lg px-2 py-1 text-sm">
                  {Array.from({ length: 24 }, (_, i) => <option key={i} value={i}>{pad(i)}h</option>)}
                </select>
                <select value={dbExport.db_export_minute}
                  onChange={e => dbExportPatch({ db_export_minute: Number(e.target.value) })}
                  className="bg-slate-700 border border-slate-600 text-white rounded-lg px-2 py-1 text-sm">
                  {[0, 15, 30, 45].map(m => <option key={m} value={m}>{pad(m)}min</option>)}
                </select>
                <span className="text-xs text-slate-500">— retenção fixa: 7 arquivos</span>
              </div>
            </div>

            {/* Upload remoto semanal */}
            <div className="border-t border-slate-700 pt-4 space-y-3">
              <label className="flex items-center gap-2 cursor-pointer text-sm">
                <input
                  type="checkbox"
                  checked={!!dbExport.db_export_remote_enabled}
                  onChange={e => dbExportPatch({ db_export_remote_enabled: e.target.checked })}
                  className="accent-violet-500"
                />
                <Cloud size={14} className="text-violet-400" />
                <span className="text-slate-300">Upload semanal pra nuvem de segurança</span>
              </label>
              <div className={`grid grid-cols-1 sm:grid-cols-2 gap-3 ${!dbExport.db_export_remote_enabled ? 'opacity-50' : ''}`}>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Protocolo</label>
                  <select value={dbExport.db_export_remote_protocolo}
                    onChange={e => {
                      const novo = e.target.value
                      dbExportPatch({
                        db_export_remote_protocolo: novo,
                        // Auto-troca porta padrão ao mudar protocolo (admin pode editar depois)
                        db_export_remote_porta: novo === 'sftp' ? 22 : 21,
                      })
                    }}
                    disabled={!dbExport.db_export_remote_enabled}
                    className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm">
                    <option value="sftp">SFTP (criptografado)</option>
                    <option value="ftp">FTP (legado, senha em claro)</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Porta</label>
                  <input type="number" min="1" max="65535"
                    value={dbExport.db_export_remote_porta}
                    onChange={e => dbExportPatch({ db_export_remote_porta: e.target.value })}
                    disabled={!dbExport.db_export_remote_enabled}
                    className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm" />
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-xs text-slate-400 mb-1">Host</label>
                  <input type="text" placeholder="backup.example.com"
                    value={dbExport.db_export_remote_host || ''}
                    onChange={e => dbExportPatch({ db_export_remote_host: e.target.value })}
                    disabled={!dbExport.db_export_remote_enabled}
                    className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm font-mono" />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Usuário</label>
                  <input type="text"
                    value={dbExport.db_export_remote_user || ''}
                    onChange={e => dbExportPatch({ db_export_remote_user: e.target.value })}
                    disabled={!dbExport.db_export_remote_enabled}
                    className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm" />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">
                    Senha {dbExport.db_export_remote_senha_configurada && <span className="text-emerald-400">· configurada</span>}
                  </label>
                  <div className="relative">
                    <input type={mostrarSenhaExport ? 'text' : 'password'}
                      value={dbExportSenha}
                      placeholder={dbExport.db_export_remote_senha_configurada ? '(deixe vazio pra manter)' : 'digite a senha'}
                      onChange={e => setDbExportSenha(e.target.value)}
                      disabled={!dbExport.db_export_remote_enabled}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 pr-9 text-sm" />
                    <button type="button" onClick={() => setMostrarSenhaExport(!mostrarSenhaExport)}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-white">
                      {mostrarSenhaExport ? <EyeOff size={14} /> : <Eye size={14} />}
                    </button>
                  </div>
                  {dbExport.db_export_remote_senha_configurada && (
                    <button type="button" onClick={() => setDbExportSenha('apagar')}
                      className="text-[11px] text-red-400 hover:text-red-300 mt-1">
                      {dbExportSenha === 'apagar' ? '✓ será removida ao salvar' : 'Remover senha salva'}
                    </button>
                  )}
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-xs text-slate-400 mb-1">Caminho remoto</label>
                  <input type="text" placeholder="/nexus-backups/"
                    value={dbExport.db_export_remote_path || '/'}
                    onChange={e => dbExportPatch({ db_export_remote_path: e.target.value })}
                    disabled={!dbExport.db_export_remote_enabled}
                    className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm font-mono" />
                </div>
                <div>
                  <label className="block text-xs text-slate-400 mb-1">Dia da semana</label>
                  <select value={dbExport.db_export_remote_dia_semana}
                    onChange={e => dbExportPatch({ db_export_remote_dia_semana: Number(e.target.value) })}
                    disabled={!dbExport.db_export_remote_enabled}
                    className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2 text-sm">
                    <option value={0}>Segunda</option>
                    <option value={1}>Terça</option>
                    <option value={2}>Quarta</option>
                    <option value={3}>Quinta</option>
                    <option value={4}>Sexta</option>
                    <option value={5}>Sábado</option>
                    <option value={6}>Domingo</option>
                  </select>
                </div>
                <div className="flex gap-2">
                  <div className="flex-1">
                    <label className="block text-xs text-slate-400 mb-1">Hora</label>
                    <select value={dbExport.db_export_remote_hora}
                      onChange={e => dbExportPatch({ db_export_remote_hora: Number(e.target.value) })}
                      disabled={!dbExport.db_export_remote_enabled}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-2 py-2 text-sm">
                      {Array.from({ length: 24 }, (_, i) => <option key={i} value={i}>{pad(i)}h</option>)}
                    </select>
                  </div>
                  <div className="flex-1">
                    <label className="block text-xs text-slate-400 mb-1">Min</label>
                    <select value={dbExport.db_export_remote_minute}
                      onChange={e => dbExportPatch({ db_export_remote_minute: Number(e.target.value) })}
                      disabled={!dbExport.db_export_remote_enabled}
                      className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-2 py-2 text-sm">
                      {[0, 15, 30, 45].map(m => <option key={m} value={m}>{pad(m)}</option>)}
                    </select>
                  </div>
                </div>
              </div>
            </div>

            {/* Arquivos locais existentes */}
            {dbExport.arquivos_locais && dbExport.arquivos_locais.length > 0 && (
              <div className="border-t border-slate-700 pt-4">
                <p className="text-xs text-slate-400 mb-2">Arquivos locais ({dbExport.arquivos_locais.length}/7):</p>
                <div className="space-y-1 text-xs font-mono">
                  {dbExport.arquivos_locais.slice(0, 7).map(f => (
                    <div key={f.nome} className="flex items-center justify-between text-slate-400 bg-slate-900/50 px-3 py-1.5 rounded">
                      <span>{f.nome}</span>
                      <span className="text-slate-500">{(f.tamanho_bytes / 1024).toFixed(1)} KB</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {dbExportMessage && (
              <p className={`text-sm ${dbExportMessage.type === 'success' ? 'text-emerald-400' : 'text-red-400'}`}>
                {dbExportMessage.text}
              </p>
            )}

            <div className="flex flex-col sm:flex-row gap-2 pt-2 border-t border-slate-700">
              <button type="button" onClick={salvarDbExport} disabled={dbExportSaving}
                className="flex items-center justify-center gap-2 px-4 py-2 bg-sky-500 hover:bg-sky-400 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors">
                <Save size={14} /> {dbExportSaving ? 'Salvando...' : 'Salvar config'}
              </button>
              <button type="button" onClick={executarExportAgora}
                disabled={executandoExport || !dbExport.chave_configurada}
                className="flex items-center justify-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors">
                <Download size={14} /> {executandoExport ? 'Exportando...' : 'Exportar agora'}
              </button>
              <button type="button" onClick={executarUploadAgora}
                disabled={executandoUpload || !dbExport.db_export_remote_enabled}
                className="flex items-center justify-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors">
                <Cloud size={14} /> {executandoUpload ? 'Enviando...' : 'Enviar agora'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Atualização do sistema (v2.3.0) — canais LTS/Edge + changelog + comando SSH */}
      <UpdateCard />
    </div>
  )
}
