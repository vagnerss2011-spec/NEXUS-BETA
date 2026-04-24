import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// --- Empresa context (localStorage) ---
export function getCurrentEmpresa() {
  try { return JSON.parse(localStorage.getItem('currentEmpresa') || 'null') }
  catch { return null }
}
export function setCurrentEmpresa(e) {
  if (e) localStorage.setItem('currentEmpresa', JSON.stringify(e))
  else localStorage.removeItem('currentEmpresa')
}
export function getUser() {
  try { return JSON.parse(localStorage.getItem('user') || 'null') }
  catch { return null }
}

// Endpoints que são escopados por empresa (para admin master enviamos ?empresa_id=)
// Obs.: /users fica de fora — admin master deve ver TODOS os usuários (masters + admins de todas as empresas)
const SCOPED_PATHS = ['/devices', '/backups']
function isScoped(url) {
  if (!url) return false
  const u = url.split('?')[0]
  return SCOPED_PATHS.some(p => u === p || u.startsWith(p + '/') || u === p + '/')
}

api.interceptors.request.use(cfg => {
  const token = localStorage.getItem('token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`

  const user = getUser()
  const emp = getCurrentEmpresa()
  // Só injeta o filtro quando é admin master com empresa selecionada
  if (user?.role === 'admin' && emp?.id && isScoped(cfg.url) && cfg.method === 'get') {
    cfg.params = { ...(cfg.params || {}), empresa_id: emp.id }
  }
  return cfg
})

api.interceptors.response.use(
  r => r,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default api
