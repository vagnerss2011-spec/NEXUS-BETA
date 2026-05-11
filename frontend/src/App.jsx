import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Login from './pages/Login'
import ChangePassword from './pages/ChangePassword'
import Dashboard from './pages/Dashboard'
import Empresas from './pages/Empresas'
import Devices from './pages/Devices'
import Backups from './pages/Backups'
import Users from './pages/Users'
import Settings from './pages/Settings'
import Logs from './pages/Logs'
import Novidades from './pages/Novidades'
import Layout from './components/Layout'
import { getCurrentEmpresa, getUser } from './services/api'

function PrivateRoute({ children }) {
  return localStorage.getItem('token') ? children : <Navigate to="/login" replace />
}

// Admin master precisa ter escolhido uma empresa antes de abrir Devices/Backups
// (Dashboard e Users não exigem — Dashboard mostra visão agregada; Users lista todas as empresas)
function RequireEmpresa({ children }) {
  const user = getUser()
  const emp = getCurrentEmpresa()
  if (user?.role === 'admin' && !emp?.id) {
    return <Navigate to="/empresas" replace />
  }
  return children
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/trocar-senha" element={<PrivateRoute><ChangePassword /></PrivateRoute>} />
        <Route path="/" element={<PrivateRoute><Layout /></PrivateRoute>}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="empresas" element={<Empresas />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="devices" element={<RequireEmpresa><Devices /></RequireEmpresa>} />
          <Route path="backups" element={<RequireEmpresa><Backups /></RequireEmpresa>} />
          <Route path="users" element={<Users />} />
          <Route path="logs" element={<Logs />} />
          <Route path="novidades" element={<Novidades />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
