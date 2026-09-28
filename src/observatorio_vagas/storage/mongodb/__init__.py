"""Implementações de armazenamento específicas do MongoDB."""

from observatorio_vagas.storage.mongodb.anuncios import (
    ConflitoAnuncioMongoDB,
    ErroRepositorioAnunciosMongoDB,
    RepositorioAnunciosMongoDB,
)
from observatorio_vagas.storage.mongodb.coletas import (
    ConflitoExecucaoColetaMongoDB,
    ErroRepositorioColetasMongoDB,
    RepositorioColetasMongoDB,
)
from observatorio_vagas.storage.mongodb.connection import (
    ConexaoMongoDB,
    ErroConexaoMongoDB,
)
from observatorio_vagas.storage.mongodb.documents import (
    documento_para_modelo,
    modelo_para_documento,
)
from observatorio_vagas.storage.mongodb.empresas import (
    ConflitoEmpresaMongoDB,
    ErroRepositorioEmpresasMongoDB,
    RepositorioEmpresasMongoDB,
)
from observatorio_vagas.storage.mongodb.publicacoes import (
    ConflitoPublicacaoMongoDB,
    ErroRepositorioPublicacoesMongoDB,
    RepositorioPublicacoesEmpregosMongoDB,
    TransicaoPublicacaoMongoDBInvalida,
)
from observatorio_vagas.storage.mongodb.schema import preparar_banco
from observatorio_vagas.storage.mongodb.vagas import (
    ConflitoVagaMongoDB,
    ErroRepositorioVagasMongoDB,
    RepositorioVagasMongoDB,
)

__all__ = [
    "ConexaoMongoDB",
    "ConflitoAnuncioMongoDB",
    "ConflitoEmpresaMongoDB",
    "ConflitoExecucaoColetaMongoDB",
    "ConflitoVagaMongoDB",
    "ConflitoPublicacaoMongoDB",
    "ErroConexaoMongoDB",
    "ErroRepositorioAnunciosMongoDB",
    "ErroRepositorioColetasMongoDB",
    "ErroRepositorioEmpresasMongoDB",
    "ErroRepositorioVagasMongoDB",
    "ErroRepositorioPublicacoesMongoDB",
    "RepositorioAnunciosMongoDB",
    "RepositorioColetasMongoDB",
    "RepositorioEmpresasMongoDB",
    "RepositorioVagasMongoDB",
    "RepositorioPublicacoesEmpregosMongoDB",
    "TransicaoPublicacaoMongoDBInvalida",
    "documento_para_modelo",
    "modelo_para_documento",
    "preparar_banco",
]
