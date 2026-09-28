"""Tipos e comportamentos compartilhados pelos modelos de domínio."""

from datetime import UTC, datetime
from typing import Annotated, Any, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

# Estes aliases evitam repetir a mesma validação em todos os modelos.
# Sempre que um campo usa TextoObrigatorio, o Pydantic remove espaços no
# começo/fim e rejeita uma string vazia.
TextoObrigatorio: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
# Confiança vai de 0 a 1. Assim, 0.90 pode ser mostrado como 90%.
Confianca: TypeAlias = Annotated[float, Field(ge=0, le=1)]

# Datas operacionais precisam conter fuso para não ficarem ambíguas quando
# servidores ou fontes estiverem em regiões diferentes.
DataHora: TypeAlias = AwareDatetime

# Permite preservar metadados extras de uma resposta externa sem perder dados.
ObjetoJson: TypeAlias = dict[str, Any]


def agora_utc() -> datetime:
    """Gera data e hora com fuso UTC para valores padrão."""

    # UTC é a referência interna. A interface poderá converter para o fuso do
    # usuário sem alterar a informação armazenada.
    return datetime.now(UTC)


class ModeloDominio(BaseModel):
    """Base estrita para evitar campos silenciosamente ignorados."""

    model_config = ConfigDict(
        # Um erro de digitação em um campo deve falhar imediatamente.
        # Sem isso, "salairo" poderia ser ignorado e o salário seria perdido.
        extra="forbid",
        # Remove espaços acidentais dos textos recebidos das fontes.
        str_strip_whitespace=True,
        # Se um campo mudar depois da criação, ele será validado novamente.
        validate_assignment=True,
    )
