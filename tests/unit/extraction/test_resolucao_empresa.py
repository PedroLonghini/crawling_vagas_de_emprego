"""Testes da resolução e associação de empresas."""

from observatorio_vagas.domain.anuncio import AnuncioVaga
from observatorio_vagas.domain.empresa import Empresa
from observatorio_vagas.domain.enums import Fonte
from observatorio_vagas.extraction.resolucao_empresa import (
    ErroResolucaoEmpresa,
    extrair_empresa_do_anuncio,
    resolver_e_associar_empresa,
)


class RepositorioEmpresasMemoria:
    """Repositório mínimo usado sem acessar o MongoDB."""

    def __init__(self) -> None:
        self.empresas: dict[object, Empresa] = {}
        self.gravacoes = 0

    def salvar(
        self,
        empresa: Empresa,
    ) -> Empresa:
        """Armazena uma empresa somente na memória do teste."""

        self.empresas[empresa.id] = empresa
        self.gravacoes += 1
        return empresa

    def buscar_por_id(
        self,
        empresa_id: object,
    ) -> Empresa | None:
        """Procura uma empresa pelo identificador."""

        return self.empresas.get(empresa_id)

    def buscar_por_cnpj(
        self,
        cnpj: str,
    ) -> Empresa | None:
        """Procura uma empresa pelo CNPJ."""

        return next(
            (empresa for empresa in self.empresas.values() if empresa.cnpj == cnpj),
            None,
        )

    def buscar_por_dominio(
        self,
        dominio: str,
    ) -> Empresa | None:
        """Procura uma empresa pelo domínio do site."""

        return next(
            (empresa for empresa in self.empresas.values() if empresa.dominio == dominio),
            None,
        )


class RepositorioAnunciosMemoria:
    """Armazena anúncios somente durante o teste."""

    def __init__(self) -> None:
        self.anuncios: dict[object, AnuncioVaga] = {}
        self.gravacoes = 0

    def salvar(
        self,
        anuncio: AnuncioVaga,
    ) -> AnuncioVaga:
        """Salva o anúncio recebido na memória."""

        self.anuncios[anuncio.id] = anuncio
        self.gravacoes += 1
        return anuncio


def criar_anuncio(
    *,
    nome_empresa: str | None = ("Oak Hill Academy West London"),
) -> AnuncioVaga:
    """Cria um anúncio parecido com o registro real do piloto."""

    organizacao: dict[str, object] = {
        "@type": "Organization",
        "sameAs": "http://www.oakhill-aspirations.org/",
        "identifier": "140718",
        "description": None,
        "logo": "/assets/logo.png",
    }

    if nome_empresa is not None:
        organizacao["name"] = nome_empresa

    return AnuncioVaga(
        fonte=Fonte.OUTRA,
        id_externo="vaga-001",
        url=("https://teaching-vacancies.service.gov.uk/jobs/vaga-001"),
        titulo_original="Teaching Assistant",
        descricao_original="Descrição da oportunidade.",
        empresa_original=nome_empresa,
        hash_conteudo="a" * 64,
        referencia_bruta="corpos/vaga-001.bin",
        campos_estruturados={
            "@type": "JobPosting",
            "industry": "Education",
            "hiringOrganization": organizacao,
            "jobLocation": {
                "@type": "Place",
                "address": {
                    "@type": "PostalAddress",
                    "addressCountry": "GB",
                },
            },
        },
    )


def test_extrai_empresa_sem_confundir_identifier_com_cnpj() -> None:
    """Um identificador britânico não pode virar CNPJ."""

    anuncio = criar_anuncio()

    primeira = extrair_empresa_do_anuncio(anuncio)

    segunda = extrair_empresa_do_anuncio(anuncio)

    # O mesmo anúncio deve sempre produzir o mesmo UUID.
    assert primeira.id == segunda.id

    assert primeira.nome_exibicao == "Oak Hill Academy West London"

    assert primeira.dominio == "oakhill-aspirations.org"

    assert str(primeira.site) == "http://www.oakhill-aspirations.org/"

    assert primeira.setor == "Education"
    assert primeira.pais == "GB"

    # O identificador 140718 não é um CNPJ.
    assert primeira.cnpj is None

    # A URL relativa do logotipo não deve ser armazenada
    # como se fosse uma URL pública completa.
    assert primeira.logo_url is None


def test_cria_empresa_e_associa_anuncio() -> None:
    """Uma empresa nova deve ser salva e ligada ao anúncio."""

    empresas = RepositorioEmpresasMemoria()
    anuncios = RepositorioAnunciosMemoria()
    anuncio = criar_anuncio()

    resultado = resolver_e_associar_empresa(
        anuncio,
        repositorio_empresas=empresas,
        repositorio_anuncios=anuncios,
    )

    assert resultado.empresa_criada is True
    assert resultado.empresa_atualizada is False
    assert resultado.anuncio_atualizado is True

    assert resultado.anuncio.empresa_id == resultado.empresa.id

    assert len(empresas.empresas) == 1
    assert empresas.gravacoes == 1
    assert anuncios.gravacoes == 1


def test_reprocessamento_reutiliza_empresa_pelo_dominio() -> None:
    """Executar novamente não deve duplicar a empresa."""

    empresas = RepositorioEmpresasMemoria()
    anuncios = RepositorioAnunciosMemoria()

    primeiro_anuncio = criar_anuncio()

    primeiro = resolver_e_associar_empresa(
        primeiro_anuncio,
        repositorio_empresas=empresas,
        repositorio_anuncios=anuncios,
    )

    segundo = resolver_e_associar_empresa(
        criar_anuncio(),
        repositorio_empresas=empresas,
        repositorio_anuncios=anuncios,
    )

    assert primeiro.empresa.id == segundo.empresa.id
    assert segundo.empresa_criada is False
    assert len(empresas.empresas) == 1


def test_reutiliza_empresa_existente_e_preserva_dados_confirmados() -> None:
    """A coleta não deve apagar dados confirmados."""

    empresas = RepositorioEmpresasMemoria()
    anuncios = RepositorioAnunciosMemoria()

    existente = Empresa(
        razao_social="Oak Hill Academy Trust",
        nome_fantasia="Oak Hill",
        dominio="oakhill-aspirations.org",
        descricao="Descrição confirmada manualmente.",
        pais="GB",
    )

    empresas.salvar(existente)

    resultado = resolver_e_associar_empresa(
        criar_anuncio(),
        repositorio_empresas=empresas,
        repositorio_anuncios=anuncios,
    )

    assert resultado.empresa.id == existente.id

    assert resultado.empresa.descricao == "Descrição confirmada manualmente."

    assert resultado.empresa.site is not None

    assert "Oak Hill Academy West London" in resultado.empresa.nomes_alternativos


def test_rejeita_anuncio_sem_nome_da_empresa() -> None:
    """Sem nome não existe evidência suficiente para criar empresa."""

    empresas = RepositorioEmpresasMemoria()
    anuncios = RepositorioAnunciosMemoria()

    try:
        resolver_e_associar_empresa(
            criar_anuncio(nome_empresa=None),
            repositorio_empresas=empresas,
            repositorio_anuncios=anuncios,
        )

    except ErroResolucaoEmpresa as erro:
        assert "não possui nome" in str(erro)

    else:
        raise AssertionError("era esperado um ErroResolucaoEmpresa")


class RepositorioAnunciosMemoriaComLote(RepositorioAnunciosMemoria):
    """Acrescenta a gravação em lote usada pela resolução em lote."""

    def __init__(self) -> None:
        super().__init__()
        self.lotes = 0

    def salvar_lote(self, anuncios):
        self.lotes += 1
        for anuncio in anuncios:
            self.anuncios[anuncio.id] = anuncio


def _anuncio_variado(
    indice: int,
    *,
    nome: str | None,
    site: str | None,
    logo: str | None = None,
    descricao: str | None = None,
) -> AnuncioVaga:
    organizacao: dict[str, object] = {"@type": "Organization"}
    if nome is not None:
        organizacao["name"] = nome
    if site is not None:
        organizacao["sameAs"] = site
    if logo is not None:
        organizacao["logo"] = logo
    if descricao is not None:
        organizacao["description"] = descricao

    return AnuncioVaga(
        fonte=Fonte.OUTRA,
        id_externo=f"vaga-{indice:03d}",
        url=f"https://empresa.example/jobs/{indice}",
        titulo_original="Vaga",
        descricao_original="Descrição da oportunidade.",
        empresa_original=nome,
        hash_conteudo="a" * 64,
        referencia_bruta=f"corpos/{indice}.bin",
        campos_estruturados={"@type": "JobPosting", "hiringOrganization": organizacao},
    )


def _cenario() -> list[AnuncioVaga]:
    """Mesma empresa com nomes e dados variados, outra empresa e um anúncio inválido."""

    return [
        _anuncio_variado(1, nome="Acme", site="https://acme.example/"),
        _anuncio_variado(
            2,
            nome="ACME Ltda",
            site="https://www.acme.example/",
            logo="https://acme.example/logo.png",
        ),
        _anuncio_variado(3, nome="Acme", site="https://acme.example/", descricao="Fabricante"),
        _anuncio_variado(4, nome="Beta", site=None),
        _anuncio_variado(5, nome=None, site=None),
        _anuncio_variado(6, nome="Beta", site=None, descricao="Serviços"),
        _anuncio_variado(7, nome="Gama", site="https://gama.example/"),
    ]


def _estado(empresas: RepositorioEmpresasMemoria, anuncios) -> tuple[object, object]:
    ignorar = {"criado_em", "atualizado_em"}
    return (
        {i: e.model_dump(exclude=ignorar) for i, e in empresas.empresas.items()},
        {a.id_externo: a.empresa_id for a in anuncios.anuncios.values()},
    )


def test_resolucao_em_lote_igual_a_um_por_um() -> None:
    """Resolver em lote deixa o banco no mesmo estado que resolver um por um."""

    from observatorio_vagas.extraction.resolucao_empresa import (
        CacheEmpresas,
        resolver_e_associar_empresas_em_lote,
    )

    # Uma empresa já existente, que deve ser reutilizada e completada.
    existente = extrair_empresa_do_anuncio(
        _anuncio_variado(99, nome="Gama SA", site="https://gama.example/")
    )

    um_por_um_empresas = RepositorioEmpresasMemoria()
    um_por_um_empresas.salvar(existente)
    um_por_um_anuncios = RepositorioAnunciosMemoria()
    falhas_um_por_um = 0
    for anuncio in _cenario():
        try:
            resolver_e_associar_empresa(
                anuncio,
                repositorio_empresas=um_por_um_empresas,
                repositorio_anuncios=um_por_um_anuncios,
            )
        except ErroResolucaoEmpresa:
            falhas_um_por_um += 1

    lote_empresas = RepositorioEmpresasMemoria()
    lote_empresas.salvar(existente)
    gravacoes_iniciais = lote_empresas.gravacoes
    lote_anuncios = RepositorioAnunciosMemoriaComLote()
    cache = CacheEmpresas.vazio()
    resultado = resolver_e_associar_empresas_em_lote(
        _cenario(),
        repositorio_empresas=lote_empresas,
        repositorio_anuncios=lote_anuncios,
        cache=cache,
    )

    assert _estado(lote_empresas, lote_anuncios) == _estado(um_por_um_empresas, um_por_um_anuncios)
    assert len(resultado.falhas) == falhas_um_por_um == 1
    assert len(resultado.anuncios) == 6
    assert all(anuncio.empresa_id is not None for anuncio in resultado.anuncios)

    # Uma gravação por empresa (Acme, Beta criadas; Gama completada) e um lote de anúncios.
    assert lote_empresas.gravacoes - gravacoes_iniciais == 3
    assert lote_anuncios.lotes == 1

    # Segundo alvo do mesmo lote: o cache evita reconsultar e regravar a empresa.
    gravacoes = lote_empresas.gravacoes
    resolver_e_associar_empresas_em_lote(
        [_anuncio_variado(10, nome="Acme", site="https://acme.example/")],
        repositorio_empresas=lote_empresas,
        repositorio_anuncios=lote_anuncios,
        cache=cache,
    )
    assert lote_empresas.gravacoes == gravacoes
