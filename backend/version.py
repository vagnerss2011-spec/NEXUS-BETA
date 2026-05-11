# Versão do backend — fonte única consultada pelo /api/version/check.
#
# Bumpar JUNTO com frontend/package.json a cada release (ver RELEASING.md).
# Se ficarem desincronizados, o banner de update no painel pode mostrar
# "Versão X disponível" enquanto o frontend já está em X — barulho na UI.
APP_VERSION = "1.3.3"
