# Contexto do projeto para continuar em outro chat

Atualizado em 2026-10-07. Cole este arquivo no início do novo chat e diga o que quer
fazer. Repositório: `C:\Users\Inspirion\Documents\Codex\2026-08-18\https-github-com-pedrolonghini-web-scrapping`
(Windows, PowerShell; Python em `.venv\Scripts\python.exe`). Responder em português,
curto e direto, explicando em linguagem simples.

## 1. O projeto
Crawler de vagas do Empregos.com (pacote `observatorio_vagas`). Fluxo: catálogo de
fontes → coleta (Scrapy) → respostas brutas em `data/raw/` (+ cadernos JSONL por
coleta) → extração → leitura dos 24 campos da API → MongoDB (anúncios, empresas,
vagas canônicas) → fila do Empregos → payloads JSON. Explicação técnica completa do
fluxo, da verificação de fontes e do JavaScript (Playwright/Chromium, só com
`--javascript`, só em listagem sem vagas) está nesta conversa resumida abaixo.

## 2. Regras que não mudam
- **Nunca publicar na API sem confirmação explícita do usuário.** O dev pediu: publicar
  1 vaga, conferir no site, depois o resto. Nada foi publicado até agora.
- Payload: `company.nationalRegister` = "00.000.000/0000-00" em todas; empresa oculta =
  `confidential` (c minúsculo).
- Respeitar robots.txt e ritmo; não contornar login/CAPTCHA/WAF; Empregos é destino.
- Apagar dados do Mongo só com confirmação e sempre com backup (`outputs/backup_mongo/`).
- `config/lote_10mil/` e `config/lote_10mil_triado/` são listas licenciadas do usuário:
  nunca vão para o git.
- Rodar `ruff format` só nos arquivos tocados (formatar a pasta mexe em arquivos alheios).

## 3. Git
Branch `melhorias-leitura-desempenho`, tudo enviado ao GitHub
(https://github.com/PedroLonghini/crawling_vagas_de_emprego). Último commit: `e35d8d6`.
1.021 testes passam (`.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider`).

## 4. Fontes
- Lista completa: `config/lote_10mil/catalogo_fontes.csv` + `fontes_autorizadas.csv`
  (~14.450 URLs, coluna `url`). Para adicionar: colar no fim dos dois arquivos (ou mandar
  a planilha para o Claude limpar duplicadas) e refazer a triagem:
  `.venv\Scripts\python.exe scripts\triar_catalogo.py --catalogo config\lote_10mil\catalogo_fontes.csv --saida-dir config\lote_10mil_triado --com-mongo`
- Catálogo usado nas coletas: `config/lote_10mil_triado/catalogo_fontes.csv` (~12.140).
  A triagem tira PDF, tag de blog, produto/loja/curso, descrição de cargo, editorial sem
  anúncio, sites do exterior e entradas repetidas de site lido inteiro (detectado pelos
  anúncios).
- Blacklist (`domain/politica_fonte.py`, vale para coleta e publicação): Empregos, Indeed,
  InfoJobs, Catho, Bluy, Cia de Estágios, Vagas.com, NIC.br, **empregandobrasil.com.br**,
  **gupy.io** (decisões do usuário em 06/10). LinkedIn é ignorado.
- Agregadores: `extraction/agregadores.py` (lista fixa) + `config/agregadores_detectados.csv`
  (gerado por `scripts/detectar_agregadores.py`: sites com 10+ empresas nos anúncios).

## 5. Comandos
- Abrir o túnel do Mongo (VM no VirtualBox ligada antes):
  `ssh -p 2222 -N -L 27019:127.0.0.1:27017 longhini@127.0.0.1`
- Coleta diária: `powershell -ExecutionPolicy Bypass -File scripts\rotina_diaria.ps1`
  (coleta 24h com limite 200/fonte, gera o lote e `outputs\logs\<data>_resumo.txt`;
  não publica; ainda NÃO agendada).
- Volta do túnel (tudo em ordem): `powershell -ExecutionPolicy Bypass -File scripts\volta_do_tunel.ps1`
  (apaga Gupy, relê desde 01/10, apaga empresas órfãs, gera lote).
- Lote oficial: `scripts\preparar_fila_empregos.py --catalogo config\lote_10mil_triado\catalogo_fontes.csv --somente-catalogo --limite 50000 --saida-json ... --diretorio-payloads ...`
- Prévia sem Mongo (mesmas regras da fila): `.venv\Scripts\python.exe scripts\previa_lote_sem_mongo.py`
- Remover uma fonte do Mongo (backup antes): `scripts\remover_fonte_do_mongo.py --dominio X --confirmar`
- Remover empresas sem uso: `scripts\remover_empresas_orfas.py --confirmar`
- Coleção para o Compass: `scripts\atualizar_vagas_republicaveis.py`
- `outputs/LEIA_PRIMEIRO.md` explica a pasta de saídas. Coisas antigas foram movidas para
  `...\2026-08-18\lixo_outputs_2026-10-07\` (pode apagar).

## 6. Números de referência
- Coleta completa 06/10 (9.442 fontes): 4h52 (download 4h15, leitura 13 min, Mongo 23 min);
  40 mil anúncios; ler fichas leva segundos (cadernos) ou minutos (índice por pasta).
- Prévia sem Mongo da coleta de 06/10: 21.957 vagas prontas; 13% `confidential`;
  12% sem UF. employed.com.br = 42% do lote (**decisão pendente: manter ou bloquear**).
- Auditoria de 30 vagas (skill `auditoria-extracao-vagas`, antes das correções de
  07/10 à tarde): 33% sem erro nos 6 obrigatórios; 27% não eram vagas; descrição 50%.
  Correções feitas depois: reportagem não vira vaga, empresa em `class="company"` e
  microdata com `content`, JSON-LD resumido perde para o bloco completo, jornal não vira
  empresa. Ainda passam: sejatrainee, moneyreport, pfarma, relvaverde; amanha.com.br e
  jornaldebarueri cortam a descrição em 500 caracteres na própria fonte.
- Candidatas à 1ª publicação de teste: Cooperativa Santa Clara (Carlos Barbosa, RS),
  Sandvik (Parauapebas, PA), ApoioEcolimp (São Paulo, vence 16/10). Arquivos em
  `outputs/verificacao/auditoria_30_vagas/`.

## 7. Estado em 07/10 à tarde
- Releitura da coleta de 06/10 rodando (começou 11:54, quase no fim às 13:03); ela
  começou antes das últimas correções, então o ideal é rodar o `volta_do_tunel.ps1`
  depois.
- Pendências no Mongo: apagar Gupy (110 anúncios), apagar empresas órfãs depois da
  releitura (296 empresas com nome de portal já classificadas em
  `outputs/verificacao/empresas_com_nome_de_portal_classificadas.csv`).

## 8. Próximos passos (ordem combinada)
1. Rodar `volta_do_tunel.ps1`, depois nova prévia e nova auditoria (meta: 90% corretas).
2. Publicar **1 vaga de teste** (mostrar o JSON e esperar o "sim" do usuário).
3. Perguntar ao dev **como a API remove/fecha uma vaga** (necessário para vagas removidas).
4. Implementar: relatório de saúde por fonte + quarentena automática (site que mudou de
   estrutura) e conferência diária das vagas publicadas (404/410, redirecionamento,
   "vaga encerrada", validade vencida) → encerrar e tirar do ar.
5. Decidir sobre o employed.com.br; agendar a rotina diária depois da 1ª publicação.
6. Máquina/túnel estáveis para produção (hoje depende do notebook).
