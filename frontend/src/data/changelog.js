// Lista de novidades exibidas na página "Novidades" do painel.
//
// Linguagem aqui é PRA LEIGO — explica O QUE muda do ponto de vista de quem
// USA o painel, não o que muda no código. Detalhe técnico fica no
// CHANGELOG.md da raiz do repo (esse sim é pro time de dev).
//
// Estrutura de cada entrada:
//   data:        "AAAA-MM-DD" — usada pra ordenar (mais recente primeiro)
//   tipo:        'novidade' | 'melhoria' | 'correcao'
//   icone:       nome do ícone lucide-react (ver imports em Novidades.jsx)
//   titulo:      manchete curta (máx ~60 chars)
//   descricao:   1-3 frases, sem jargão técnico
//   categoria:   string curta pra agrupar visualmente (ex.: "Mikrotik", "ZTE")
//
// Pra adicionar entrada nova: empurra no TOPO do array.

export const CHANGELOG_ENTRIES = [
  // ===== Maio/2026 — Mikrotik via API =====
  {
    data: '2026-05-11',
    tipo: 'novidade',
    icone: 'Wifi',
    categoria: 'Mikrotik',
    titulo: 'Backup de Mikrotik via API',
    descricao:
      'Agora você pode coletar backup de roteadores e switches Mikrotik usando a API oficial do RouterOS, sem precisar abrir SSH no equipamento. ' +
      'Funciona tanto em versões mais antigas (v6) quanto nas novas (v7), e dá pra ativar TLS pra criptografar a conexão.',
  },
  {
    data: '2026-05-11',
    tipo: 'novidade',
    icone: 'Clock',
    categoria: 'Mikrotik',
    titulo: 'Ajuste automático de horário em Mikrotik',
    descricao:
      'Ao cadastrar um Mikrotik com protocolo API, o sistema configura automaticamente o NTP cliente (apontando pro servidor do NEXUS) e o fuso horário pra America/Sao_Paulo. ' +
      'Roda uma vez só, no momento do cadastro — não mexe na configuração depois.',
  },
  {
    data: '2026-05-11',
    tipo: 'melhoria',
    icone: 'Trash2',
    categoria: 'Mikrotik',
    titulo: 'Limpeza automática de arquivos temporários',
    descricao:
      'Antes de cada backup via API, o sistema remove arquivos temporários que sobraram de coletas anteriores no Mikrotik. ' +
      'Evita que a memória do equipamento encha sem que ninguém perceba.',
  },

  // ===== Maio/2026 — ZTE C3XX =====
  {
    data: '2026-05-10',
    tipo: 'novidade',
    icone: 'Terminal',
    categoria: 'ZTE',
    titulo: 'Suporte oficial a OLTs ZTE C3XX',
    descricao:
      'Família ZTE C300/C320/C600 (firmware ZXA10) agora aparece como "ZTE C3XX" no painel — fica claro que é diferente da linha nova C6XX Titan. ' +
      'A coleta é feita via Telnet, único protocolo onde o equipamento entrega a configuração inteira sem cortes.',
  },

  // ===== Maio/2026 — Identidade visual =====
  {
    data: '2026-05-10',
    tipo: 'melhoria',
    icone: 'Image',
    categoria: 'Visual',
    titulo: 'Nova logomarca do NEXUS BACKUP',
    descricao:
      'O ícone genérico que estava como provisório foi substituído pela logomarca oficial do NEXUS BACKUP, aplicada no menu lateral, na tela de login e como favicon da aba do navegador. ' +
      'Crédito autoral discreto no rodapé do menu: "Idealizado e testado por Vagner — innetsolutions.com.br".',
  },

  // ===== Maio/2026 — Notificação de update =====
  {
    data: '2026-05-10',
    tipo: 'novidade',
    icone: 'BellRing',
    categoria: 'Sistema',
    titulo: 'Aviso de versão nova disponível',
    descricao:
      'Quando sai uma versão nova do NEXUS BACKUP, o painel mostra um aviso no topo com um botão "Ver mudanças" que abre o resumo do que foi alterado. ' +
      'A atualização em si continua sendo feita manualmente pelo administrador — o aviso só serve pra não passar despercebida.',
  },

  // ===== Maio/2026 — Logs do Sistema =====
  {
    data: '2026-05-10',
    tipo: 'melhoria',
    icone: 'ScrollText',
    categoria: 'Sistema',
    titulo: 'Logs do Sistema agora mostram tudo num lugar só',
    descricao:
      'A página "Logs" reúne em ordem cronológica tanto as execuções do agendador diário (02:00) quanto os eventos de recebimento de backup por push (FTP/SFTP/TFTP). ' +
      'Dá pra filtrar por tipo, ver qual protocolo recebeu cada arquivo e apagar eventos antigos por linha ou em massa.',
  },

  // ===== Abril/2026 — Lançamento estável =====
  {
    data: '2026-04-01',
    tipo: 'novidade',
    icone: 'Sparkles',
    categoria: 'Lançamento',
    titulo: 'Primeira versão estável do NEXUS BACKUP',
    descricao:
      'Painel completo pra gerenciar backup de equipamentos de rede de várias marcas: MikroTik, Huawei, Ubiquiti, ZTE, Nokia, Fiberhome, Intelbras e outros. ' +
      'Suporta múltiplas empresas isoladas, usuários com permissões diferentes (admin, operador, viewer), coleta automática diária, retenção configurável e notificações por Telegram quando alguma coisa falha.',
  },
  {
    data: '2026-04-01',
    tipo: 'novidade',
    icone: 'Upload',
    categoria: 'Lançamento',
    titulo: 'Recebimento de backup via FTP, SFTP e TFTP',
    descricao:
      'Além de coletar o backup ativamente via SSH/Telnet, o NEXUS aceita que o próprio equipamento envie a configuração pra ele. ' +
      'Útil pra OLTs Huawei MA5800, ZTE C320, Fiberhome via UNM2000 e qualquer device que tenha "export config" agendado pelo lado deles.',
  },
  {
    data: '2026-04-01',
    tipo: 'novidade',
    icone: 'Bell',
    categoria: 'Lançamento',
    titulo: 'Alertas no Telegram quando algo dá errado',
    descricao:
      'Você cadastra um bot do Telegram e o painel envia mensagem automática quando um backup falha, quando alguém tenta enviar arquivo sem autorização, ou quando o volume de uploads ficar muito alto. ' +
      'Cada empresa pode ter um chat diferente recebendo só os alertas dela.',
  },
]
