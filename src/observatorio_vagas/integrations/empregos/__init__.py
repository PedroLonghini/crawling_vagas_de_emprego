"""Integração com a API de vagas do Empregos."""

from observatorio_vagas.integrations.empregos.client import (
    AutenticacaoEmpregosRecusada,
    ClienteEmpregos,
    ConfiguracaoClienteEmpregosInvalida,
    ErroClienteEmpregos,
    FalhaTransporteEmpregos,
    RequisicaoEmpregosRecusada,
    RespostaTransporteEmpregos,
    ResultadoEmpregosIndeterminado,
    ResultadoEnvioEmpregos,
    SituacaoEnvioEmpregos,
    TransporteEmpregos,
    TransporteRequestsEmpregos,
)
from observatorio_vagas.integrations.empregos.fila import (
    ItemFilaEmpregos,
    MotivoFilaEmpregos,
    ResultadoFilaEmpregos,
    SituacaoItemFilaEmpregos,
    preparar_fila_empregos,
)
from observatorio_vagas.integrations.empregos.lote import (
    ItemResultadoLoteEmpregos,
    ResultadoPublicacaoLoteEmpregos,
    SituacaoResultadoLoteEmpregos,
    publicar_fila_empregos,
)
from observatorio_vagas.integrations.empregos.payload import (
    PayloadEmpregosInvalido,
    gerar_json_empregos,
    gerar_payload_empregos,
)
from observatorio_vagas.integrations.empregos.preparacao import (
    ProvenienciaFontePublicacao,
    PublicacaoEmpregosBloqueada,
    ResultadoPreparacaoEmpregos,
    preparar_publicacao_empregos,
)
from observatorio_vagas.integrations.empregos.publicador import (
    OperacaoPublicacaoEmpregosBloqueada,
    PublicadorEmpregos,
    ResultadoPublicacaoEmpregosPersistida,
)

__all__ = [
    "AutenticacaoEmpregosRecusada",
    "ClienteEmpregos",
    "ConfiguracaoClienteEmpregosInvalida",
    "ErroClienteEmpregos",
    "FalhaTransporteEmpregos",
    "ItemFilaEmpregos",
    "ItemResultadoLoteEmpregos",
    "MotivoFilaEmpregos",
    "PayloadEmpregosInvalido",
    "OperacaoPublicacaoEmpregosBloqueada",
    "PublicacaoEmpregosBloqueada",
    "ProvenienciaFontePublicacao",
    "PublicadorEmpregos",
    "RequisicaoEmpregosRecusada",
    "RespostaTransporteEmpregos",
    "ResultadoEmpregosIndeterminado",
    "ResultadoEnvioEmpregos",
    "ResultadoFilaEmpregos",
    "ResultadoPreparacaoEmpregos",
    "ResultadoPublicacaoEmpregosPersistida",
    "ResultadoPublicacaoLoteEmpregos",
    "SituacaoEnvioEmpregos",
    "SituacaoItemFilaEmpregos",
    "SituacaoResultadoLoteEmpregos",
    "TransporteEmpregos",
    "TransporteRequestsEmpregos",
    "gerar_json_empregos",
    "gerar_payload_empregos",
    "preparar_publicacao_empregos",
    "preparar_fila_empregos",
    "publicar_fila_empregos",
]
