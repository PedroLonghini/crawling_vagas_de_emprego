"""Modelos e regras centrais do Observatório de Vagas."""

# Anúncio exatamente como foi encontrado na fonte.
from observatorio_vagas.domain.anuncio import (
    AnuncioVaga,
)

# Informações sobre uma execução do crawler.
from observatorio_vagas.domain.coleta import (
    ExecucaoColeta,
    MetricasColeta,
)

# Regras que decidem se uma vaga pode ser publicada.
from observatorio_vagas.domain.elegibilidade import (
    BloqueioPublicacao,
    CodigoBloqueioPublicacao,
    ResultadoElegibilidadePublicacao,
    avaliar_elegibilidade_publicacao,
)

# Empresa identificada ou enriquecida pelo sistema.
from observatorio_vagas.domain.empresa import (
    Empresa,
    EmpresaFonte,
    EvidenciaCadastralEmpresa,
)

# Evidências que explicam de onde um valor foi extraído.
from observatorio_vagas.domain.evidencias import (
    EvidenciaExtracao,
)

# Histórico de mudanças observadas nos anúncios.
from observatorio_vagas.domain.historico import (
    AlteracaoCampo,
    ObservacaoAnuncio,
)

# Operação idempotente destinada à API do Empregos.
from observatorio_vagas.domain.publicacao import (
    OperacaoPublicacaoEmpregos,
    calcular_chave_idempotencia_empregos,
)

# Recrutador relacionado à empresa.
from observatorio_vagas.domain.recrutador import (
    Recrutador,
)

# Vaga normalizada e salário convertido.
from observatorio_vagas.domain.vaga import (
    SalarioNormalizado,
    VagaCanonica,
)

# __all__ informa quais nomes formam a interface pública
# do pacote observatorio_vagas.domain.
__all__ = [
    "AlteracaoCampo",
    "AnuncioVaga",
    "BloqueioPublicacao",
    "CodigoBloqueioPublicacao",
    "Empresa",
    "EmpresaFonte",
    "EvidenciaCadastralEmpresa",
    "EvidenciaExtracao",
    "ExecucaoColeta",
    "MetricasColeta",
    "ObservacaoAnuncio",
    "OperacaoPublicacaoEmpregos",
    "Recrutador",
    "ResultadoElegibilidadePublicacao",
    "SalarioNormalizado",
    "VagaCanonica",
    "avaliar_elegibilidade_publicacao",
    "calcular_chave_idempotencia_empregos",
]
