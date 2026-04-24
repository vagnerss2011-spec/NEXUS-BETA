export default function StatusBadge({ status }) {
  const map = {
    sucesso: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
    falha: 'bg-red-500/20 text-red-400 border border-red-500/30',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium capitalize ${map[status] || 'bg-slate-700 text-slate-300'}`}>
      {status}
    </span>
  )
}
