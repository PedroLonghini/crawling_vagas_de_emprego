"""Enriquece uma empresa com um CNPJ extraído de um PDF coletado.

Por segurança, o comando funciona em modo de prévia.
A gravação no MongoDB exige a opção --confirmar.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from observatorio_vagas.config import get_settings
from observatorio_vagas.crawling.inventario import (
    ErroInventarioBruto,
    RegistroInventarioBruto,
    carregar_inventario_bruto,
)
from observatorio_vagas.crawling.raw_storage import ler_corpo_bruto
from observatorio_vagas.domain.common import agora_utc
from observatorio_vagas.domain.empresa import (
    Empresa,
    EvidenciaCadastralEmpresa,
)
from observatorio_vagas.extraction.cnpj_documento import (
    CnpjDocumento,
    ErroExtracaoCnpjDocumento,
    extrair_cnpjs_pdf,
)
from observatorio_vagas.storage.mongodb import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
    ErroRepositorioEmpresasMongoDB,
    RepositorioEmpresasMongoDB,
)


def criar_parser() -> argparse.ArgumentParser:
    """Cria os argumentos aceitos pelo comando."""

    parser = argparse.ArgumentParser(
        description=("Extrai um CNPJ de um PDF já coletado e enriquece uma empresa no MongoDB.")
    )

    parser.add_argument(
        "--empresa-id",
        required=True,
        help="UUID da empresa que receberá o CNPJ",
    )

    parser.add_argument(
        "--alvo-id",
        required=True,
        help="alvo_id do documento PDF armazenado",
    )

    parser.add_argument(
        "--diretorio-bruto",
        help=("pasta do armazenamento bruto; quando omitida, usa OBS_RAW_STORAGE_PATH ou data/raw"),
    )

    parser.add_argument(
        "--confirmar",
        action="store_true",
        help="autoriza a gravação da empresa no MongoDB",
    )

    return parser


def converter_uuid(
    valor: str,
) -> UUID:
    """Converte um texto em UUID com uma mensagem compreensível."""

    try:
        return UUID(valor)

    except ValueError as erro:
        raise ValueError(f"empresa-id não é um UUID válido: {valor}") from erro


def selecionar_registro_pdf(
    registros: tuple[RegistroInventarioBruto, ...],
    *,
    alvo_id: str,
) -> RegistroInventarioBruto:
    """Seleciona a coleta PDF mais recente do alvo informado."""

    candidatos = [
        registro
        for registro in registros
        if registro.alvo_id == alvo_id
        and 200 <= registro.status_http < 300
        and "application/pdf" in (registro.tipo_conteudo or "").casefold()
    ]

    if not candidatos:
        raise LookupError(f"nenhum PDF coletado com sucesso foi encontrado para o alvo: {alvo_id}")

    return max(
        candidatos,
        key=lambda registro: registro.coletado_em,
    )


def carregar_corpo_verificado(
    *,
    diretorio_base: Path,
    registro: RegistroInventarioBruto,
) -> bytes:
    """Carrega o corpo e confirma tamanho e SHA-256."""

    base = diretorio_base.resolve()

    caminho = (base / registro.caminho_corpo).resolve()

    try:
        caminho.relative_to(base)

    except ValueError as erro:
        raise ValueError("o caminho do corpo está fora do armazenamento bruto") from erro

    if not caminho.is_file():
        raise FileNotFoundError(f"corpo bruto não encontrado: {registro.caminho_corpo}")

    conteudo = ler_corpo_bruto(caminho)

    if len(conteudo) != registro.tamanho_bytes:
        raise ValueError(
            f"o tamanho do corpo não corresponde aos metadados da coleta: {registro.referencia}"
        )

    hash_calculado = sha256(conteudo).hexdigest()

    if hash_calculado != registro.hash_conteudo:
        raise ValueError(
            f"o SHA-256 do corpo não corresponde aos metadados da coleta: {registro.referencia}"
        )

    return conteudo


def selecionar_cnpj_unico(
    conteudo: bytes,
) -> CnpjDocumento:
    """Exige exatamente um CNPJ distinto no documento."""

    resultados = extrair_cnpjs_pdf(conteudo)

    if not resultados:
        raise LookupError("nenhum CNPJ válido foi encontrado no PDF")

    cnpjs_distintos = {resultado.cnpj for resultado in resultados}

    if len(cnpjs_distintos) > 1:
        encontrados = ", ".join(sorted(cnpjs_distintos))

        raise ValueError(
            "o PDF possui mais de um CNPJ distinto; "
            f"nenhum será escolhido automaticamente: {encontrados}"
        )

    return resultados[0]


def validar_identidade_empresa(
    *,
    empresa: Empresa,
    registro: RegistroInventarioBruto,
) -> None:
    """Confirma que o alvo foi catalogado para a empresa selecionada."""

    if registro.empresa_nome is None:
        raise ValueError("o registro bruto não informa empresa_nome")

    nomes_empresa = (
        empresa.razao_social,
        empresa.nome_fantasia,
        *empresa.nomes_alternativos,
    )

    nomes_normalizados = {
        nome.strip().casefold() for nome in nomes_empresa if nome is not None and nome.strip()
    }

    nome_catalogo = registro.empresa_nome.strip().casefold()

    if nome_catalogo not in nomes_normalizados:
        raise ValueError(
            "a empresa informada no catálogo não corresponde à empresa selecionada no MongoDB"
        )


def evidencia_ja_existe(
    empresa: Empresa,
    *,
    cnpj: str,
    registro: RegistroInventarioBruto,
    pagina: int,
) -> bool:
    """Verifica se a mesma prova já foi registrada."""

    return any(
        evidencia.campo.casefold() == "cnpj"
        and evidencia.valor_extraido == cnpj
        and evidencia.hash_conteudo == registro.hash_conteudo
        and evidencia.pagina == pagina
        for evidencia in empresa.evidencias_cadastrais
    )


def preparar_empresa_atualizada(
    *,
    empresa: Empresa,
    registro: RegistroInventarioBruto,
    resultado: CnpjDocumento,
) -> tuple[Empresa, bool]:
    """Cria uma nova versão validada da empresa."""

    if empresa.cnpj is not None and empresa.cnpj != resultado.cnpj:
        raise ValueError(
            "a empresa já possui um CNPJ diferente; a atualização automática foi bloqueada"
        )

    repetida = evidencia_ja_existe(
        empresa,
        cnpj=resultado.cnpj,
        registro=registro,
        pagina=resultado.pagina,
    )

    cnpj_alterado = empresa.cnpj != resultado.cnpj

    if not cnpj_alterado and repetida:
        return empresa, False

    evidencias = empresa.evidencias_cadastrais

    if not repetida:
        nova_evidencia = EvidenciaCadastralEmpresa(
            campo="cnpj",
            valor_extraido=resultado.cnpj,
            url_fonte=registro.url_final,
            referencia_bruta=registro.caminho_corpo,
            hash_conteudo=registro.hash_conteudo,
            pagina=resultado.pagina,
            trecho_evidencia=resultado.trecho_evidencia,
            coletado_em=registro.coletado_em,
        )

        evidencias = (
            *evidencias,
            nova_evidencia,
        )

    dados = empresa.model_dump(
        mode="python",
    )

    dados["cnpj"] = resultado.cnpj
    dados["evidencias_cadastrais"] = evidencias
    dados["atualizado_em"] = agora_utc()

    empresa_atualizada = Empresa.model_validate(dados)

    return empresa_atualizada, True


def exibir_previa(
    *,
    empresa: Empresa,
    empresa_atualizada: Empresa,
    registro: RegistroInventarioBruto,
    resultado: CnpjDocumento,
    confirmar: bool,
    possui_alteracao: bool,
) -> None:
    """Mostra exatamente o que poderá ser gravado."""

    modo = "GRAVAÇÃO CONFIRMADA" if confirmar else "SOMENTE PRÉVIA"

    print()
    print("# ENRIQUECIMENTO CADASTRAL DA EMPRESA")
    print()
    print(f"Modo: {modo}")
    print(f"Empresa ID: {empresa.id}")
    print(f"Empresa: {empresa.nome_exibicao}")
    print(f"Alvo ID: {registro.alvo_id}")
    print(f"URL da evidência: {registro.url_final}")
    print(f"Referência bruta: {registro.caminho_corpo}")
    print(f"SHA-256: {registro.hash_conteudo}")
    print(f"CNPJ atual: {empresa.cnpj or 'não informado'}")
    print(f"CNPJ encontrado: {resultado.formatado}")
    print(f"Página: {resultado.pagina}")
    print(f"Evidências atuais: {len(empresa.evidencias_cadastrais)}")
    print(f"Evidências depois da operação: {len(empresa_atualizada.evidencias_cadastrais)}")
    print()
    print("Trecho da evidência:")
    print(resultado.trecho_evidencia)

    if not possui_alteracao:
        print()
        print("O CNPJ e a evidência já estão armazenados.")

    elif not confirmar:
        print()
        print("Nenhuma alteração será feita no MongoDB.")
        print("Revise os dados e execute novamente com --confirmar.")


def executar(
    argumentos: list[str] | None = None,
) -> int:
    """Executa a extração, validação e possível gravação."""

    parser = criar_parser()
    opcoes = parser.parse_args(argumentos)

    try:
        empresa_id = converter_uuid(opcoes.empresa_id)

        configuracoes = get_settings()

        diretorio_bruto = (
            Path(opcoes.diretorio_bruto)
            if opcoes.diretorio_bruto
            else configuracoes.raw_storage_path
        )

        registros = carregar_inventario_bruto(diretorio_bruto)

        registro = selecionar_registro_pdf(
            registros,
            alvo_id=opcoes.alvo_id,
        )

        conteudo = carregar_corpo_verificado(
            diretorio_base=diretorio_bruto,
            registro=registro,
        )

        resultado = selecionar_cnpj_unico(conteudo)

        with ConexaoMongoDB(configuracoes) as conexao:
            repositorio = RepositorioEmpresasMongoDB(conexao.banco)

            empresa = repositorio.buscar_por_id(empresa_id)

            if empresa is None:
                raise LookupError(f"empresa não encontrada: {empresa_id}")

            validar_identidade_empresa(
                empresa=empresa,
                registro=registro,
            )

            empresa_conflitante = repositorio.buscar_por_cnpj(resultado.cnpj)

            if empresa_conflitante is not None and empresa_conflitante.id != empresa.id:
                raise ValueError(
                    f"o CNPJ encontrado já pertence a outra empresa: {empresa_conflitante.id}"
                )

            empresa_atualizada, possui_alteracao = preparar_empresa_atualizada(
                empresa=empresa,
                registro=registro,
                resultado=resultado,
            )

            exibir_previa(
                empresa=empresa,
                empresa_atualizada=empresa_atualizada,
                registro=registro,
                resultado=resultado,
                confirmar=opcoes.confirmar,
                possui_alteracao=possui_alteracao,
            )

            if opcoes.confirmar and possui_alteracao:
                repositorio.salvar(empresa_atualizada)

                print()
                print("Empresa atualizada com sucesso.")
                print(f"CNPJ armazenado: {resultado.formatado}")

        return 0

    except (
        ErroConexaoMongoDB,
        ErroExtracaoCnpjDocumento,
        ErroInventarioBruto,
        ErroRepositorioEmpresasMongoDB,
        LookupError,
        OSError,
        ValueError,
    ) as erro:
        print(f"ERRO: {erro}")
        return 1


if __name__ == "__main__":
    raise SystemExit(executar())
