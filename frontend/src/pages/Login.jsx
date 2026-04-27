import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Shield, Loader2 } from 'lucide-react'
import api, { setCurrentEmpresa } from '../services/api'

export default function Login() {
  const [email, setEmail] = useState('')
  const [senha, setSenha] = useState('')
  const [erro, setErro] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function handleSubmit(e) {
    e.preventDefault()
    setErro('')
    setLoading(true)
    try {
      const form = new URLSearchParams()
      form.append('username', email)
      form.append('password', senha)
      const { data } = await api.post('/auth/login', form)
      localStorage.setItem('token', data.access_token)
      const me = await api.get('/auth/me')
      localStorage.setItem('user', JSON.stringify(me.data))

      // Senha temporária: força troca antes de tudo
      if (me.data.senha_temporaria) {
        navigate('/trocar-senha')
        return
      }

      if (me.data.role === 'admin') {
        // admin master escolhe a empresa; limpa contexto anterior
        setCurrentEmpresa(null)
        navigate('/empresas')
      } else {
        // demais roles: empresa fixa do próprio usuário
        if (me.data.empresa_id) {
          try {
            const emp = await api.get(`/empresas/${me.data.empresa_id}`)
            setCurrentEmpresa({ id: emp.data.id, nome: emp.data.nome })
          } catch {
            setCurrentEmpresa({ id: me.data.empresa_id, nome: '' })
          }
        }
        navigate('/dashboard')
      }
    } catch (err) {
      const status = err?.response?.status
      const detail = err?.response?.data?.detail
      if (status === 429) {
        setErro('Muitas tentativas em pouco tempo. Aguarde 1 minuto e tente de novo.')
      } else if (status === 423) {
        setErro(detail || 'Conta bloqueada temporariamente por excesso de tentativas.')
      } else if (status === 403) {
        setErro(detail || 'Usuário inativo')
      } else {
        setErro('E-mail ou senha inválidos')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-900">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-sky-500/20 mb-4">
            <Shield className="text-sky-400" size={28} />
          </div>
          <h1 className="text-2xl font-bold text-white">NEXUS BETA</h1>
          <p className="text-slate-400 text-sm mt-1">Backup Manager — Acesso restrito</p>
        </div>

        <form onSubmit={handleSubmit} className="bg-slate-800 rounded-2xl p-8 border border-slate-700 space-y-4">
          {erro && (
            <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm px-4 py-2.5 rounded-lg">
              {erro}
            </div>
          )}
          <div>
            <label className="block text-sm text-slate-400 mb-1.5">E-mail</label>
            <input
              type="email" value={email} onChange={e => setEmail(e.target.value)} required
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors"
              placeholder="seu@email.com"
            />
          </div>
          <div>
            <label className="block text-sm text-slate-400 mb-1.5">Senha</label>
            <input
              type="password" value={senha} onChange={e => setSenha(e.target.value)} required
              className="w-full bg-slate-900 border border-slate-600 rounded-lg px-3 py-2.5 text-white text-sm focus:outline-none focus:border-sky-500 transition-colors"
              placeholder="••••••••"
            />
          </div>
          <button type="submit" disabled={loading}
            className="w-full bg-sky-500 hover:bg-sky-400 disabled:opacity-60 text-white font-semibold py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2 mt-2">
            {loading && <Loader2 size={16} className="animate-spin" />}
            Entrar
          </button>
        </form>
      </div>
    </div>
  )
}
