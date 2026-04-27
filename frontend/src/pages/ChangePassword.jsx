import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shield, Loader2, Eye, EyeOff, KeyRound } from 'lucide-react'
import api from '../services/api'

// Tela acessada quando o backend marca o usuário com senha_temporaria=True.
// Bloqueia o resto do app via interceptor no axios + redirect no Login.
export default function ChangePassword() {
  const [senhaAtual, setSenhaAtual] = useState('')
  const [senhaNova, setSenhaNova] = useState('')
  const [senhaConfirma, setSenhaConfirma] = useState('')
  const [mostrar, setMostrar] = useState(false)
  const [erro, setErro] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  function validar() {
    if (senhaNova.length < 8) return 'Nova senha precisa ter pelo menos 8 caracteres'
    if (senhaNova === senhaAtual) return 'A nova senha precisa ser diferente da atual'
    if (senhaNova !== senhaConfirma) return 'A confirmação não confere com a nova senha'
    return null
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setErro('')
    const erroLocal = validar()
    if (erroLocal) { setErro(erroLocal); return }
    setLoading(true)
    try {
      const { data: novoUser } = await api.post('/auth/change-password', {
        senha_atual: senhaAtual,
        senha_nova: senhaNova,
      })
      // Atualiza o snapshot local (senha_temporaria agora é false)
      localStorage.setItem('user', JSON.stringify(novoUser))
      // Admin master vai pra tela de empresas; demais pro dashboard
      navigate(novoUser.role === 'admin' ? '/empresas' : '/dashboard')
    } catch (err) {
      const detail = err?.response?.data?.detail
      // O endpoint pode retornar string ou array de erros (validation Pydantic)
      let msg = 'Falha ao trocar a senha'
      if (typeof detail === 'string') msg = detail
      else if (Array.isArray(detail)) msg = detail[0]?.msg || msg
      setErro(msg)
    } finally {
      setLoading(false)
    }
  }

  function logout() {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    navigate('/login')
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-amber-500/20 mb-4">
            <KeyRound className="text-amber-400" size={28} />
          </div>
          <h1 className="text-2xl font-bold text-white">Defina sua nova senha</h1>
          <p className="text-slate-400 text-sm mt-1">
            Antes de continuar, troque a senha temporária por uma sua.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="bg-slate-800 rounded-2xl p-8 border border-slate-700 space-y-4">
          {erro && (
            <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm px-4 py-2.5 rounded-lg">
              {erro}
            </div>
          )}
          <div>
            <label className="block text-sm text-slate-400 mb-1.5">Senha atual (a temporária)</label>
            <input
              type={mostrar ? 'text' : 'password'}
              value={senhaAtual} onChange={e => setSenhaAtual(e.target.value)} required
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none focus:border-amber-500 transition-colors"
            />
          </div>
          <div>
            <label className="block text-sm text-slate-400 mb-1.5">Nova senha (mín. 8 caracteres)</label>
            <input
              type={mostrar ? 'text' : 'password'}
              value={senhaNova} onChange={e => setSenhaNova(e.target.value)} required minLength={8}
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none focus:border-amber-500 transition-colors"
            />
          </div>
          <div>
            <label className="block text-sm text-slate-400 mb-1.5">Confirme a nova senha</label>
            <input
              type={mostrar ? 'text' : 'password'}
              value={senhaConfirma} onChange={e => setSenhaConfirma(e.target.value)} required
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none focus:border-amber-500 transition-colors"
            />
          </div>
          <label className="flex items-center gap-2 text-xs text-slate-400 cursor-pointer select-none">
            <input
              type="checkbox" checked={mostrar} onChange={e => setMostrar(e.target.checked)}
              className="accent-amber-500"
            />
            {mostrar ? <Eye size={14} /> : <EyeOff size={14} />}
            Mostrar senhas
          </label>
          <button type="submit" disabled={loading}
            className="w-full bg-amber-500 hover:bg-amber-400 disabled:opacity-60 text-slate-900 font-semibold py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2 mt-2">
            {loading && <Loader2 size={16} className="animate-spin" />}
            Salvar nova senha
          </button>
          <button type="button" onClick={logout}
            className="w-full text-slate-500 hover:text-slate-300 text-xs underline transition-colors">
            Sair sem trocar
          </button>
        </form>

        <div className="mt-4 text-center text-xs text-slate-500 flex items-center justify-center gap-1">
          <Shield size={12} /> Senhas criptografadas com bcrypt e transmitidas via HTTPS.
        </div>
      </div>
    </div>
  )
}
