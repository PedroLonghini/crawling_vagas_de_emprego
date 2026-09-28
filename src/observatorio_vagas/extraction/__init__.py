"""Ferramentas responsáveis por extrair e normalizar dados."""

from observatorio_vagas.extraction.ckan import (
    ResultadoExtracaoCkan,
    extrair_job_postings_ckan,
)
from observatorio_vagas.extraction.geolocalizacao_html import (
    CoordenadasHtml,
    enriquecer_job_posting_com_geolocalizacao,
    extrair_coordenadas_html,
)
from observatorio_vagas.extraction.json_ld import (
    ResultadoExtracaoJsonLd,
    extrair_job_postings_json_ld,
)
from observatorio_vagas.extraction.mapeamento_json_ld import (
    converter_job_posting_em_anuncio,
)
from observatorio_vagas.extraction.metadados_empresa import (
    MetadadosEmpresaHtml,
    enriquecer_job_posting_com_empresa,
    extrair_metadados_empresa_html,
    extrair_metadados_site_institucional_html,
)
from observatorio_vagas.extraction.normalizacao_vaga import (
    ErroNormalizacaoVaga,
    converter_anuncio_em_vaga_canonica,
)
from observatorio_vagas.extraction.noticias_vagas import (
    ResultadoExtracaoNoticiasVagas,
    extrair_job_postings_noticia_itaqui,
    extrair_job_postings_noticia_palotina24h,
)
from observatorio_vagas.extraction.portal_publico import (
    ResultadoExtracaoPortalPublico,
    extrair_job_postings_portal_publico,
)
from observatorio_vagas.extraction.processador import (
    FalhaProcessamentoExtracao,
    ResultadoProcessamentoExtracao,
    processar_respostas_brutas,
)
from observatorio_vagas.extraction.querido_diario import (
    ResultadoExtracaoQueridoDiario,
    extrair_job_postings_querido_diario,
)
from observatorio_vagas.extraction.resolucao_empresa import (
    ConflitoResolucaoEmpresa,
    ErroResolucaoEmpresa,
    ResultadoResolucaoEmpresa,
    extrair_empresa_do_anuncio,
    resolver_e_associar_empresa,
)
from observatorio_vagas.extraction.url_candidatura import (
    UrlCandidaturaEncontrada,
    extrair_url_candidatura_html,
)

__all__ = [
    "ConflitoResolucaoEmpresa",
    "CoordenadasHtml",
    "ErroNormalizacaoVaga",
    "ErroResolucaoEmpresa",
    "FalhaProcessamentoExtracao",
    "MetadadosEmpresaHtml",
    "ResultadoExtracaoJsonLd",
    "ResultadoExtracaoNoticiasVagas",
    "ResultadoExtracaoCkan",
    "ResultadoExtracaoPortalPublico",
    "ResultadoExtracaoQueridoDiario",
    "ResultadoProcessamentoExtracao",
    "ResultadoResolucaoEmpresa",
    "UrlCandidaturaEncontrada",
    "converter_anuncio_em_vaga_canonica",
    "converter_job_posting_em_anuncio",
    "enriquecer_job_posting_com_empresa",
    "enriquecer_job_posting_com_geolocalizacao",
    "extrair_coordenadas_html",
    "extrair_empresa_do_anuncio",
    "extrair_job_postings_json_ld",
    "extrair_job_postings_noticia_itaqui",
    "extrair_job_postings_noticia_palotina24h",
    "extrair_job_postings_ckan",
    "extrair_job_postings_portal_publico",
    "extrair_job_postings_querido_diario",
    "extrair_metadados_empresa_html",
    "extrair_metadados_site_institucional_html",
    "extrair_url_candidatura_html",
    "processar_respostas_brutas",
    "resolver_e_associar_empresa",
]
