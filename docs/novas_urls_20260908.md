# Seis novas URLs — 08/09/2026

As seis URLs de config/urls_fontes_em_adaptacao.txt responderam HTTP 200.
Foram cadastradas sem duplicar as URLs existentes, com ativa=false no catálogo
central. O catálogo operacional continua com 515 alvos. Estas adições não
significam seis fontes já integradas nem seis vagas vigentes.

## Evidência e condições

- UFMS: o conjunto Vagas para Concurso informa Creative Commons Attribution.
  Contém recursos CSV mensais; o esquema e a vigência ainda precisam de adaptação.
- Imprensa 24h: as páginas dos concursos de Acaraú e Jarinu autorizam reprodução
  com a URL integral https://imprensa24h.com.br/ como fonte, sem abreviações.
- Palotina 24 Horas: as três páginas autorizam reprodução com manutenção do
  crédito ao Palotina 24 Horas.

Cada licenca_url aponta para a própria página que contém a condição. A permissão
foi verificada para os textos dessas páginas; não constitui liberação geral dos
domínios, das imagens, dos anexos ou dos sites de candidatura relacionados.

Os próximos conversores devem separar cargos individuais, conservar a origem,
aplicar as condições de atribuição e verificar inscrições. Uma notícia com
231 oportunidades não deve virar automaticamente 231 anúncios duplicados.
Empresa_nome no catálogo identifica o publicador, não prova quem é o empregador.

URLs de notícias são pontuais. A descoberta recorrente de novas matérias exige
um adaptador que confira a permissão em cada página encontrada. Duas outras
matérias do Imprensa 24h não apresentaram a autorização e foram excluídas.

Para consultar a lista no PowerShell:

```powershell
Get-Content config\urls_fontes_em_adaptacao.txt
```
