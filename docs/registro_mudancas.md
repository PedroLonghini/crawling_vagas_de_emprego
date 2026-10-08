# Registro de mudanças e perguntas

Atualizado a cada 3 mudanças feitas pelo Claude ou a cada 5 perguntas do usuário (regra do gbrain, combinada em 2026-10-07).

## Mudanças
- 2026-10-07: criado este registro e a regra de atualização (3 mudanças / 5 perguntas).

- 2026-10-07: gerado `docs/todos_md_juntos.md` (todos os .md concatenados, para colar em um chat).
- 2026-10-07: gerado `docs/pacote_avaliar_fonte.md` (8 .md juntos, para a skill avaliar-fonte).
- 2026-10-07: instaladas 5 skills em `.claude/skills/` (atualizar-contexto, avaliar-fonte, checar-docs, conferir-amostra, ler-cobertura), vindas de `skills-crawling.zip`.
- 2026-10-07: rodada a skill checar-docs (só leitura): 23 achados, nenhum doc editado ainda.
- 2026-10-07: aplicadas as 6 correções de gravidade alta da checar-docs: blacklist em README, config/README, biblioteca_urls e TODO agora aponta para `politica_fonte.py` (achado 4); Gupy bloqueada na coleta em `contexto_continuidade_projeto.md` (5); `fontes_verificadas_20260908.md` marcado como histórico (3); candidatas pendentes só na fila, não no catálogo, em `pesquisa_fontes_republicaveis_20260916.md` (2); portão 5 de `processo_prospeccao_fontes.md` reescrito sem colunas inexistentes (1); fluxo real de autorização em massa descrito no README (6).
- 2026-10-07: detecção de vagas removidas da origem. Novo `domain/vagas_removidas.py` (regras: coleta completa, 2 ausências seguidas, conferência da página por 404/410, redirecionamento ou aviso de encerramento); spider passa a gravar na cobertura `janela_horas`, `janela_encerrada`, `limite_anuncios`, `motivos_fim_navegacao` e `listagens_nao_visitadas`; novo `scripts/conferir_vagas_removidas.py` (prévia por padrão, `--verificar`, `--confirmar`, coleção `presenca_anuncios`, `fila_remocao_api` no relatório). 42 testes novos; suíte com 1.063 testes passando. Prévia em 06/10: 0 fontes comparáveis (relatórios antigos sem os campos novos).
- 2026-10-07: criado `scripts/rotina_semanal.ps1` (coleta sem janela com `--limite-anuncios 200` + `conferir_vagas_removidas.py --verificar` em prévia, só com as coberturas da própria coleta); conferência passa a aceitar link visto "desde o dia" (coleta que passa da meia-noite). Primeira rodada iniciada às 14:30 de 07/10. Agendamento no Windows ainda NÃO criado.
- 2026-10-07: correções da revisão da detecção de vagas removidas: (1) só compara vagas cujo link já apareceu numa listagem; (2) fonte só é completa com o spider terminado ("finished") e sem listagem sem resposta/descartada (spider grava `listagens_sem_resposta`, `listagens_descartadas`); (3) listagem inalterada (304/hash igual) não é comparável (`listagens_inalteradas`); (4) redirecionamento canônico com número não encerra; (5) aviso de encerramento só em frase sobre esta vaga e fora de menu/rodapé/barra lateral; (6) repositório de anúncios não sobrescreve status `encerrado`/`ausente_aguardando_confirmacao` numa nova leitura (encerrar continua permitido) e encerrada que segue na listagem é `reaberto`; (7) status só muda quando a página confirma o encerramento; suspeitas conferidas há mais tempo vão primeiro; rotina semanal com log UTF-8, BOM, trava contra coleta simultânea, códigos de saída e lista de coberturas salva. Suíte: 1.087 testes passando.
- 2026-10-07: gerado `docs/pacote_numeros_crawling.md` (otimizacao_crawling, roteiro_teste_10mil_mac, roteiro_medicao_mac juntos).
- 2026-10-07: nova regra de publicação (decisão do usuário): vaga publicada na fonte há mais de 30 dias é barrada (`VAGA_ANTIGA`, `IDADE_MAXIMA_PUBLICACAO_DIAS = 30` em `domain/elegibilidade.py`); sem data de publicação continua elegível. Nada apagado do Mongo. Suíte: 1.088 testes.
- 2026-10-07: blacklist indireta (decisão do usuário): vaga cujo link de candidatura vai para domínio bloqueado não é republicável (`domain/elegibilidade.py`). Afeta 8.390 anúncios (Gupy 7.834, Vagas.com 366, InfoJobs 184), sobretudo employed (7.493) e vagas.sc (775). Não pega panoramafarmaceutico (link do Vagas.com não é lido como candidatura). Usuário optou por NÃO bloquear inteiras employed, eu.dev.br, jobfy, empregospernambuco, panoramafarmaceutico. Suíte: 1.089 testes.
- 2026-10-07: exportado `outputs/verificacao/vagas_republicaveis_setembro_ate_hoje.csv` (22.070 vagas de 07/09 a 07/10, antes da blacklist indireta).
- 2026-10-08: duplicadas entre lotes: assinatura de conteúdo (`domain/assinatura_vaga.py`: título, empresa, local e começo da descrição normalizados) gravada em cada publicação (`assinatura_conteudo`); a fila barra vaga cuja assinatura já está no ar por outro site (`duplicada_de_vaga_publicada`); a deduplicação dentro do lote usa a mesma assinatura. Índice novo em `publicacoes_empregos`. Suíte: 1.096 testes. Revisão por subagente em andamento.
- 2026-10-08: correções da revisão da duplicada entre lotes: (1) mesmo site exige descrição inteira igual, sites diferentes só o começo (publicação guarda `assinatura_descricao_completa` e `dominio_origem`; repositório `listar_ativas_por_assinatura`); (2) repetida no envio em lote vira `ignorada_duplicada`, sem falha e sem ocupar o limite; (3) o próprio `PublicadorEmpregos` confere antes de reservar (`VagaJaPublicadaPorOutraFonte`), cobrindo `publicar_vaga_empregos.py`; (4) "+" e "#" mantidos no título (C++ ≠ C#). Ficam para depois: desempenho (1 consulta por vaga), liberar cópia quando a original sair do ar (depende da remoção pela API), concorrência entre duas publicações simultâneas.
- 2026-10-08: trava de publicação: `RepositorioPublicacoesEmpregosMongoDB.trava_publicacao()` (coleção `travas`, um documento, vence em 2 min, espera até 60 s); o `PublicadorEmpregos` confere duplicadas e reserva sob a trava. Testes do repositório real (`listar_ativas_por_assinatura`: rejeitada fora, mais antiga primeiro; trava ocupada, liberada após erro, vencida assumida). Suíte: 1.107 testes.
- 2026-10-08: 2ª revisão (subagente): A–D resolvidos, E parcial (testes só com fakes). Corrigido M2: no envio em lote, `VagaJaPublicadaPorOutraFonte` vira `ignorada_duplicada` e devolve a vaga no limite; `TravaPublicacaoOcupada` adia o resto do lote. Pendente M1: antes de ligar a publicação diária, confirmar que não há publicações sem assinatura (hoje 0 publicações) e criar o índice novo (`preparar_banco`). Medição: 27 cópias idênticas no mesmo site e 118 entre sites barradas; 92 falsos positivos antigos do mesmo site agora liberados. Suíte: 1.109 testes.
- 2026-10-08: varredura semanal (decisão do usuário: uma vez por semana, para não deixar passar vaga, conferir se as vagas estão no ar, conferir duplicatas e remover do Empregos automaticamente). Novo `scripts/rotina_semanal.py` (Windows e Mac; o `.ps1` virou atalho): coleta completa sem janela → vagas novas que só a varredura achou → `encerrar_anuncios_expirados.py --todos --confirmar` (banco inteiro) → `conferir_vagas_removidas.py --verificar --verificar-publicadas --confirmar` (abre a página de toda vaga publicada, antes das outras) → duplicatas entre as publicadas + fila de remoção (`integrations/empregos/remocao.py`) → aba `vagas_republicaveis`. Remoção pela API ainda SIMULADA (`RemovedorNaoConfigurado`) até o Empregos definir a API. Trava contra coleta simultânea (psutil). Suíte: 1.118 testes.
- 2026-10-08: (1) "404 disfarçado": página que responde 200 mas diz "página não encontrada", "vaga não encontrada", "erro 404", "page/job not found" etc. agora conta como encerrada (`pagina_nao_encontrada`). (2) Varredura semanal separada da diária (decisão do usuário): `processar_lote.py --estado semanal --sem-agendamento` (registro de estado próprio em `<catálogo>/.cache/semanal/` e todas as fontes, sem o adiamento da diária); a conferência lê os links vistos desse registro; `rotina_diaria.ps1` é pulada se a varredura (ou outra coleta) estiver rodando. Suíte: 1.131 testes.
- 2026-10-08: decisão do usuário: a varredura confere os anúncios dos últimos 30 dias. A coleta continua lendo as listagens inteiras; a comparação de links e a abertura de páginas de suspeitas só consideram anúncios publicados há até 30 dias ou sem data (`filtro_de_idade`, mesmo prazo de `IDADE_MAXIMA_PUBLICACAO_DIAS`; `--todas-as-idades` desliga). Vagas já publicadas no Empregos continuam conferidas em qualquer idade, e as duplicatas são procuradas entre todas as publicadas. Suíte: 1.132 testes.
- 2026-10-08: criado `scripts/atualizar_vagas_republicaveis.py` (reaproveita `preparar_fila_empregos`; grava só as elegíveis na coleção nova `vagas_republicaveis`; `--simular` não grava; `--alvo-id` limita a um alvo; quem deixou de ser elegível sai da coleção). Coleção fora de `schema.py` porque `test_schema.py` fixa a lista de coleções. Rodado: 5.354 avaliados, 2.924 republicáveis, 2.203 bloqueados, 227 duplicados; 2.924 gravadas.

## Perguntas
- 2026-10-07: "você pegou todo o contexto do projeto certo?" — resposta: sim, resumo do `contexto_projeto_para_novo_chat.md`, sem ler código nem Mongo.
- 2026-10-07: pedido da regra de registro em md (3 mudanças / 5 perguntas) — registrada.
- 2026-10-07: "como funcionam os adaptadores" — explicado: só descobrem links de vaga; registro por tipo de fonte, genérico como padrão.
- 2026-10-07: "me envie os arquivos md" — enviados 27 arquivos.
- 2026-10-07: "como coloco todos em um chat" — gerado `docs/todos_md_juntos.md`.
- 2026-10-07: "quantos anúncios/republicáveis/empresas no Mongo" — 70.482 anúncios, 2.924 republicáveis (coleção possivelmente defasada), 12.529 empresas, 0 publicações.
- 2026-10-07: pedido dos 7 .md da avaliação de fontes — enviados.
- 2026-10-07: "colocar tudo no mesmo arquivo" — gerado `docs/pacote_avaliar_fonte.md`.
- 2026-10-07: "como instalo e coloco em outro chat" — explicado (anexar ou copiar e colar).
- 2026-10-07: "colocar 5 skills nesse site" — pedi o endereço do site.
- 2026-10-07: enviou `skills-crawling.zip` — continha 6 skills.
- 2026-10-07: "comece a usar as 5 skills" — instaladas em `.claude/skills/`.
- 2026-10-07: "pode fazer o que sugerir" — rodada a checar-docs.
- 2026-10-07: "pode fazer as alterações" — aplicadas as 6 de gravidade alta.
- 2026-10-07: "remover a vaga do site pela API quando a origem remover" — API só tem POST; como remover depende do dev.
- 2026-10-07: "comparar pelo id a cada leitura ou conferir a página?" — recomendado juntar: comparação por link em coleta completa + 2 ausências + conferência só das suspeitas.
- 2026-10-07: "pode começar" — implementada a detecção (ver Mudanças).
- 2026-10-07: "faça uma coleta semanal" — criada a rotina semanal e iniciada a 1ª rodada.
- 2026-10-07: "use subagente para verificar" — revisão achou 33/33 falsos positivos na amostra; 12 achados.
- 2026-10-07: "pode fazer" — corrigidos os achados (ver Mudanças).
- 2026-10-07: pedido dos 3 .md com números de crawling — enviados juntos em `docs/pacote_numeros_crawling.md`.
- 2026-10-07: "10 fontes com mais vagas" — employed 9.991, emploive 9.737, juriscorrespondente 6.454...
- 2026-10-07: "quantos anúncios/empresas/republicáveis" — 84.506 / 15.656 / 2.924 (coleção de 06/10, defasada); coleta semanal ainda rodando.
- 2026-10-07: "gerar payloads de novo aumenta? qual período?" — sim; sem filtro de período, teto de 50 mil anúncios.
- 2026-10-07: "fonte com mais republicáveis" — employed.com.br, 9.267 (33,6%) de 27.543; 6.332 títulos com entidade HTML.
- 2026-10-07: "essas fontes são permitidas?" — sim pela lista autorizada do usuário; 3 são agregadores; sem avaliação individual de termos.
- 2026-10-07: "aba vagas_republicaveis desatualizada" — rodado `atualizar_vagas_republicaveis.py`.
- 2026-10-07: "período das vagas" — 53% até 30 dias, 16,8% com mais de 90 dias, 19,7% sem data.
- 2026-10-07: "apagar as de até 30 dias" — esclarecido: barrar na publicação as com mais de 30 dias, manter sem data; nada apagado.
- 2026-10-07: "como o crawler detecta que a fonte não é republicável" — explicado (lista autorizada, blacklist, domínio, status, regras da vaga).
- 2026-10-08: "DBeaver Community com Mongo / driver JDBC" — não compensa (JDBC oficial só Atlas SQL, nativo só no Pro); usar MongoDB Compass.
- 2026-10-08: "como acho o localhost do Mongo na VM" — Mongo escuta só em 127.0.0.1; acesso por túnel SSH (porta 2222 redirecionada no VirtualBox, IP 10.0.2.15 é NAT).
- 2026-10-08: "vagas canônicas são todas publicáveis? / o que é anúncio" — não; elegibilidade é calculada em `domain/elegibilidade.py`; anúncio = como veio da fonte, canônica = normalizada.
- 2026-10-08: "criar seção só de republicáveis" — criada a coleção `vagas_republicaveis` (ver Mudanças).
- 2026-10-08: "quanto tempo demora / onde rodo / não consigo rodar" — mais de 3 min para 5.354 anúncios pelo túnel; rodar no PowerShell do Windows, não na VM.
- 2026-10-08: "transferir minha VM para outro computador" — ver resposta no chat (exportar appliance no VirtualBox).
