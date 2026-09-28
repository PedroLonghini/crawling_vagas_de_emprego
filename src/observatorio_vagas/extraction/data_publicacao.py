"""Datas de publicação no calendário de Brasília, independentes da coleta."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

FUSO_PUBLICACAO = ZoneInfo("America/Sao_Paulo")


def ontem_brasilia(agora: datetime | None = None) -> date:
    momento = agora if agora is not None else datetime.now(FUSO_PUBLICACAO)
    if momento.tzinfo is None:
        raise ValueError("o momento de referência precisa informar o fuso")
    return momento.astimezone(FUSO_PUBLICACAO).date() - timedelta(days=1)


def dia_publicacao(valor: date | datetime | None) -> date | None:
    if isinstance(valor, datetime):
        # O MongoDB persiste um ``date`` como meia-noite UTC. Ao restaurar o
        # documento, o Pydantic pode devolvê-lo como datetime; nesse caso ele
        # continua representando a data-calendário da fonte, e não um instante
        # para conversão ao fuso de Brasília.
        if valor.tzinfo is UTC and valor.timetz().replace(tzinfo=None) == time.min:
            return valor.date()

        # Um horário sem fuso não pode ser convertido com segurança.
        return valor.astimezone(FUSO_PUBLICACAO).date() if valor.tzinfo is not None else None
    return valor if isinstance(valor, date) else None
