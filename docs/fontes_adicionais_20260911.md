# Fontes adicionais — 11/09/2026

## Data Privacy Brasil

- Entrada: https://www.dataprivacybr.org/tag/vaga/
- Organização brasileira independente, com anúncios de contratação própria.
- O rodapé da entrada e dos anúncios declara CC BY-SA 4.0.
- Coleta habilitada, adaptador `pagina_carreiras`, limite de 10 páginas.
- Crédito, URL original e licença devem acompanhar o conteúdo. Adaptações precisam respeitar o compartilhamento pela mesma licença e indicar alterações.
- Exemplo verificado: https://www.dataprivacybr.org/vaga-para-gestora-de-cursos-e-formacoes/ (prazo encerrado em 14/06/2026). Não tratar como vaga vigente.

## Fiquem Sabendo

- Entrada: https://news.fiquemsabendo.com.br/archive
- Organização brasileira independente; sua newsletter também anuncia contratações próprias.
- Evidência: https://news.fiquemsabendo.com.br/p/calamidades-e-emergencias-na-ultima
- A publicação declara CC BY 4.0 para material gratuito do site e da newsletter, incluindo uso comercial. Exige crédito institucional, link original e menções aos perfis quando houver divulgação em redes sociais.
- Cadastrada ativa após validar um adaptador que isola a seção da vaga, sem transformar a notícia inteira em descrição de emprego. Integra o lote diário e a lista operacional.
- O exemplo de estágio encerrou inscrições em 23/08/2026. A licença não se estende automaticamente a páginas externas de candidatura.

## Limites desta inclusão

Licença de conteúdo não garante vaga vigente, autorização para acessar qualquer domínio ou atendimento aos campos obrigatórios do Empregos. Manter robots, filtros de concursos, datas, atribuição e elegibilidade. Nenhuma vaga foi publicada nem gravada no MongoDB nesta alteração. A extração ao vivo das novas fontes ainda precisa ser validada.

Teste em prévia:

```powershell
.\.venv\Scripts\python.exe scripts\processar_lote.py --catalogo config\catalogo_fontes_20260911_extra.csv --coletar
```

Esse comando coleta e salva respostas brutas localmente, mas não grava anúncios no MongoDB sem `--confirmar` e não envia para o Empregos.
