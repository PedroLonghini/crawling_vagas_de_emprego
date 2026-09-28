# Novas fontes próprias — 15/09/2026

Foram cadastradas duas organizações privadas brasileiras, não órgãos públicos
nem agregadores/ATS. São empregadores institucionais, não empresas comerciais.
Não foi encontrada nesta rodada outra empresa comercial com página de vagas
operacional e permissão suficientemente clara. Fontes existentes não foram duplicadas.

## Escopo e comprovação

| Alvo | Página inicial | Evidência de licença |
| --- | --- | --- |
| nupef_oportunidades | https://nupef.org.br/noticias/ | Rodapé de https://nupef.org.br/: CC BY-SA 4.0 para conteúdo original; terceiros seguem suas próprias licenças. |
| transparencia_brasil_oportunidades | https://www.transparencia.org.br/noticias/ | Rodapé de https://www.transparencia.org.br/: conteúdo sob CC BY-SA 4.0. |

A aprovação no catálogo é condicionada ao cumprimento da licença: crédito,
link para a origem e licença, indicação de alterações e compartilhamento de
adaptações nos mesmos termos. Não é autorização irrestrita para fotos, marcas,
conteúdos atribuídos a terceiros ou material de ATS/formulários externos.
Consulte https://creativecommons.org/licenses/by-sa/4.0/deed.pt-br.

O adaptador exige título de vaga e uma afirmação de contratação pela própria
organização no artigo. Notícias gerais, vagas encerradas, concursos e menções
incidentais ao empregador não devem gerar anúncios. Layout desconhecido retorna
zero sem capturar a página inteira. Datas e CNPJ ausentes não são inventados.
Esse reconhecimento é conservador: redações diferentes poderão exigir ajuste.

## Configuração

- Limite de 10 páginas por fonte, respeitando as regras gerais de robots e carga.
- Fontes incluídas nos catálogos central, diário e republicável, e em urls_fontes.txt.
- Catálogo isolado: config/catalogo_organizacoes_novas.csv.
- Nenhuma rotina de publicação foi executada ou automação diária criada.

## Validação realizada

- Oito testes de extração/configuração passaram.
- Nupef: listagem HTTP 200, nenhum candidato de vaga no teste limitado.
- Transparência Brasil: listagem HTTP 200, nenhum candidato; o fallback genérico
  tentou /sitemap.xml, que respondeu 404. Isso não significa falha na listagem.
- Os testes reais foram limitados a duas páginas e cinco detalhes por fonte.
  Não demonstram ausência de vagas em todo o histórico nem garantem vagas abertas hoje.
- Não houve gravação no MongoDB ou chamada à API do Empregos.

Relatórios locais:

- outputs/testes_fontes/20260915T182524Z_321707a8/resumo.json
- outputs/testes_fontes/20260915T182533Z_8501aedc/resumo.json

## Repetir o teste de uma fonte

```powershell
.\.venv\Scripts\python.exe scripts\testar_fonte.py --catalogo config\catalogo_organizacoes_novas.csv --alvo-id nupef_oportunidades --paginas 10 --anuncios 20
```

Para testar a outra, substitua o alvo por transparencia_brasil_oportunidades.
Uma fonte licenciada não torna automaticamente cada anúncio elegível: os dados
obrigatórios, vigência e demais regras de publicação continuam sendo avaliados.

## Candidatas não ativadas

- Greenpeace: regras específicas restringem o uso comercial.
- Wikimedia Brasil: licença aberta, mas o acesso local retornou 403; não foi contornado.
- Antigo blog da Transparência Brasil: erro de certificado TLS; usamos apenas o domínio atual.
- Instituto Update e ARTIGO 19: restrição NãoComercial.
- 4Linux: licença identificada em outra seção não foi estendida ao blog ou ATS.
- Instituto Pólis: não foi possível comprovar a permissão de republicação da vaga.

Essas candidatas não receberam aprovação nem foram adicionadas ao catálogo operacional.
