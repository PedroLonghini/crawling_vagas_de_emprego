# Modelo de domínio

Os modelos da Entrega 2 ficam em `src/observatorio_vagas/domain` e não
dependem do banco, da API do Empregos ou de uma fonte externa específica.

## Relações principais

```text
Empresa
  └── EmpresaFonte

VagaCanonica
  └── reúne um ou mais AnuncioVaga

AnuncioVaga
  ├── possui ObservacaoAnuncio ao longo do tempo
  └── possui EvidenciaExtracao para campos estruturados ou inferidos

ExecucaoColeta
  └── produz ObservacaoAnuncio

CorrespondenciaAnuncios
  └── compara dois anúncios usando sinais explicáveis
```

## Princípios

- campos originais nunca são substituídos pelos normalizados;
- a vaga canônica é diferente do anúncio publicado em uma fonte;
- uma oportunidade pode aparecer em várias fontes;
- histórico é composto por observações, não por sobrescrita;
- correspondências possuem pontuação, sinais e versão do algoritmo;
- extrações possuem método, confiança, evidência e versão;
- salário publicado, calculado e estimado são naturezas distintas;
- datas operacionais usam fuso horário;
- o hash de conteúdo novo usa SHA-256;
- CNPJ é texto e aceita o formato alfanumérico.

## Limites desta entrega

Esta entrega define e valida os objetos em memória. Persistência, migrações,
repositórios e integração com APIs pertencem às próximas entregas.
