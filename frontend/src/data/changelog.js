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
  // ===== Maio/2026 — v2.2.1 =====
  {
    data: '2026-05-19',
    tipo: 'melhoria',
    icone: 'HardDrive',
    categoria: 'Firmwares',
    titulo: 'Mirror de firmwares: whitelist de IP + ver credencial quando quiser',
    descricao:
      'A senha da origem FTP agora pode ser consultada e copiada a qualquer momento (botão da chave na lista), sem precisar regerar. ' +
      'Cada origem aceita uma whitelist de IP(s): se preenchida, o FTP só aceita conexões daqueles endereços — segurança extra além da senha. ' +
      'Também corrigimos um travamento ao baixar via cliente FTP desktop: lembre de marcar "modo passivo" no seu cliente (devices como Mikrotik já usam passivo automaticamente).',
  },
  // ===== Maio/2026 — v2.2.0 =====
  {
    data: '2026-05-19',
    tipo: 'novidade',
    icone: 'HardDrive',
    categoria: 'Firmwares',
    titulo: 'Mirror FTP de firmwares — devices baixam do servidor',
    descricao:
      'Nova aba "Firmwares" no menu. Você sobe firmware pelo painel (Mikrotik npk, Huawei bin, etc.) e cadastra uma "origem" — o sistema gera usuário e senha de FTP. ' +
      'Os devices remotos usam essa credencial pra baixar do servidor via /tool fetch (Mikrotik) ou comando equivalente. ' +
      'Acaba com a necessidade de subir o mesmo firmware pra cada device — você sobe uma vez no painel e quantos devices quiserem baixam pelo mirror. ' +
      'A senha aparece UMA vez na criação — anote ou regere depois se perder. Cada origem pode ser ativada/desativada sem precisar excluir.',
  },
  // ===== Maio/2026 — v2.1.0 =====
  {
    data: '2026-05-15',
    tipo: 'novidade',
    icone: 'Wrench',
    categoria: 'Mikrotik',
    titulo: 'Operações Mikrotik em massa',
    descricao:
      'Nova aba "Operações" no menu lateral pra executar ações em vários Mikrotiks de uma vez só, sem precisar abrir Winbox/SSH em cada um. ' +
      'Tem checagens rápidas (versão do RouterOS, lista de usuários, inventário com modelo/uptime/espaço livre) e configurações (criar/remover usuário, configurar SNMP, limpar arquivos órfãos da NAND). ' +
      'Ações destrutivas pedem confirmação dupla (digitar "CONFIRMAR" ou "EXECUTAR") e admin master tem acesso a comando livre com bloqueio automático de comandos perigosos. ' +
      'Cada execução fica registrada no histórico com quem disparou, em quais devices e o resultado por equipamento.',
  },
  // ===== Maio/2026 — v2.0.0 =====
  {
    data: '2026-05-11',
    tipo: 'novidade',
    icone: 'ShieldCheck',
    categoria: 'Segurança',
    titulo: 'Backup criptografado do banco (.nxbak)',
    descricao:
      'Todo dia o sistema gera um arquivo criptografado com TODOS os backups armazenados — pra recuperar mesmo se o servidor queimar ou corromper. ' +
      'Uma vez por semana esse arquivo pode ser enviado pra uma "nuvem de segurança" externa (servidor SFTP ou FTP de sua escolha). ' +
      'O arquivo só abre numa ferramenta específica que conhece o formato + a chave Fernet que você guarda em local separado.',
  },
  {
    data: '2026-05-11',
    tipo: 'melhoria',
    icone: 'Cpu',
    categoria: 'Performance',
    titulo: 'Backup paralelo com auto-ajuste',
    descricao:
      'O backup diário das 02:00 agora coleta múltiplos equipamentos ao mesmo tempo em vez de um por vez. ' +
      'O sistema mede CPU e memória continuamente e ajusta sozinho quantos rodam em paralelo — começa devagar, acelera quando vê que aguenta e diminui se notar que está sobrecarregado. ' +
      'Resultado: backup de muitos devices termina mais rápido, sem risco de travar o servidor.',
  },
  {
    data: '2026-05-11',
    tipo: 'melhoria',
    icone: 'Clock',
    categoria: 'Performance',
    titulo: 'Pausa adaptativa entre coletas',
    descricao:
      'Entre uma coleta e a próxima, o sistema espera um tempo proporcional à duração da anterior — backups longos ganham mais pausa antes do próximo começar. ' +
      'Evita congestionamento e dá tempo do equipamento "respirar" entre comandos. Configurável em Configurações.',
  },
  {
    data: '2026-05-11',
    tipo: 'melhoria',
    icone: 'AlertTriangle',
    categoria: 'Confiabilidade',
    titulo: 'Detecção de picos e backups encolhidos',
    descricao:
      'Se um equipamento de repente leva muito mais tempo que de costume pra fazer backup, o sistema avisa por Telegram e marca no log. ' +
      'Mesmo aviso quando o backup vem com tamanho muito menor que o anterior (pode estar truncado ou corrompido). ' +
      'Ajuda a pegar problema cedo, antes que vire perda de dado.',
  },
  {
    data: '2026-05-11',
    tipo: 'novidade',
    icone: 'Hand',
    categoria: 'Dispositivos',
    titulo: 'Backup somente manual em equipamentos específicos',
    descricao:
      'Marca a checkbox "Somente backup manual" no cadastro de um dispositivo e ele para de entrar no agendamento automático diário. ' +
      'Útil pra equipamentos de lab/teste que você só quer coletar quando clicar no botão. A retenção de 7 backups continua valendo normalmente.',
  },
  {
    data: '2026-05-11',
    tipo: 'novidade',
    icone: 'ArrowDownUp',
    categoria: 'Interface',
    titulo: 'Ordenação clicável na tabela de Dispositivos',
    descricao:
      'Agora você pode clicar em qualquer coluna da tabela de Dispositivos (ID, Nome, IP, Tipo, Fabricante, Protocolo, Ativo, Último backup) pra ordenar por aquela coluna. ' +
      'Clicar de novo inverte a direção. IPs são ordenados naturalmente (192.168.1.2 antes de 192.168.1.10) e IPv6 sempre vai pro fim.',
  },
  {
    data: '2026-05-11',
    tipo: 'novidade',
    icone: 'Trash2',
    categoria: 'Backups',
    titulo: 'Excluir todos os backups com falha de uma vez',
    descricao:
      'Botão novo na página Backups que apaga todos os backups com status "falha" do seu escopo. ' +
      'Útil pra limpar a lista depois de uma queda de internet que deixou dezenas de devices com erro. Backups bem-sucedidos não são afetados.',
  },
  {
    data: '2026-05-11',
    tipo: 'novidade',
    icone: 'Megaphone',
    categoria: 'Interface',
    titulo: 'Página Novidades no menu',
    descricao:
      'Este menu novo onde você está lendo agora. Resumo amigável de tudo o que muda no NEXUS BACKUP — sem precisar olhar o CHANGELOG técnico do código.',
  },

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
