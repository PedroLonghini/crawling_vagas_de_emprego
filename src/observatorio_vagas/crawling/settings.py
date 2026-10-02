"""Configurações seguras do crawler Scrapy."""

# Nome interno do projeto dentro do Scrapy.
BOT_NAME = "observatorio_vagas"

# Pasta onde ficarão os crawlers específicos de cada fonte.
SPIDER_MODULES = [
    "observatorio_vagas.crawling.spiders",
]

# Local usado pelo comando que cria novos spiders.
NEWSPIDER_MODULE = "observatorio_vagas.crawling.spiders"

# Diretório usado durante o desenvolvimento para armazenar
# o HTML, JSON e outros conteúdos originais coletados.
#
# Essa pasta é adequada para o piloto.
# Em produção, ela poderá ser substituída por S3, MinIO
# ou outro armazenamento preparado para milhões de arquivos.
RAW_STORAGE_DIRECTORY = "data/raw"


# Pipelines recebem os objetos produzidos pelos spiders.
#
# O número 100 representa a prioridade de execução.
# Números menores são executados primeiro.
ITEM_PIPELINES = {
    "observatorio_vagas.crawling.pipelines.PipelineArmazenamentoBruto": 100,
}


# Respeita as regras publicadas no arquivo robots.txt de cada site.
ROBOTSTXT_OBEY = True

# Identifica nosso crawler sem fingir ser um navegador comum.
#
# Antes da produção, adicionaremos um endereço de contato corporativo.
USER_AGENT = "ObservatorioVagas/0.1.0"

# Não mantém cookies por padrão.
#
# Isso reduz rastreamento e evita criar sessões desnecessárias.
COOKIES_ENABLED = False

# Desativa o console Telnet do Scrapy.
#
# Não precisaremos desse console e não queremos deixá-lo
# disponível acidentalmente em um servidor.
TELNETCONSOLE_ENABLED = False


# Mais sites diferentes em paralelo, sem aumentar a pressão por domínio.
# Como cada domínio continua limitado a uma requisição, 180 permite avançar
# fontes independentes enquanto outras aguardam respostas lentas. O ajuste
# usa principalmente conexões em espera, não processamento contínuo de CPU.
CONCURRENT_REQUESTS = 180

# Duas requisições simultâneas por domínio, no máximo (ritmo de ~2 por
# segundo). Os portais grandes têm slots próprios mais abaixo.
CONCURRENT_REQUESTS_PER_DOMAIN = 2

# Espera mínima entre requisições para o mesmo domínio.
DOWNLOAD_DELAY = 0.5

# Varia levemente o intervalo para evitar rajadas regulares.
RANDOMIZE_DOWNLOAD_DELAY = True

# As fontes Abler aprovadas possuem uma listagem própria e adaptador dedicado;
# não precisam de sitemap. Mantemos uma aceleração moderada e exclusiva para
# esse domínio, sem mudar o limite conservador dos demais sites.
DOWNLOAD_SLOTS = {
    "ats.abler.com.br": {
        "concurrency": 2,
        "delay": 0.5,
        "randomize_delay": True,
    },
    # APIs públicas de ATS já possuem adaptadores próprios: nelas a resposta
    # é JSON pequeno, sem renderização de navegador. Acelerar só esses slots
    # preserva o comportamento prudente para sites de empresas individuais.
    "apigw.solides.com.br": {
        "concurrency": 3,
        "delay": 0.35,
        "randomize_delay": True,
    },
    "platform.senior.com.br": {
        "concurrency": 2,
        "delay": 0.5,
        "randomize_delay": True,
    },
    "jobs.lever.co": {
        "concurrency": 2,
        "delay": 0.5,
        "randomize_delay": True,
    },
    "jobs.quickin.io": {
        "concurrency": 2,
        "delay": 0.5,
        "randomize_delay": True,
    },
    # Portais que dominam o volume do lote diário: no lote de 1.000 fontes,
    # empregandobrasil.com.br e emploive.com somaram 80% das 73 mil páginas.
    # Sonda de 2026-10-02 (10 páginas por nível): com 2 e 4 requisições não
    # houve 429, 403 nem 5xx, e o robots.txt não define Crawl-delay. Ficamos
    # em 3 requisições; os demais sites seguem em 2.
    "empregandobrasil.com.br": {
        "concurrency": 3,
        "delay": 0.35,
        "randomize_delay": True,
    },
    "emploive.com": {
        "concurrency": 3,
        "delay": 0.35,
        "randomize_delay": True,
    },
}


# AutoThrottle adapta a velocidade de acordo com
# o tempo de resposta do site.
AUTOTHROTTLE_ENABLED = True

# Espera inicial utilizada pelo AutoThrottle.
AUTOTHROTTLE_START_DELAY = 1.0

# Se o site estiver lento, o intervalo pode chegar a 30 segundos.
AUTOTHROTTLE_MAX_DELAY = 30.0

# Busca manter aproximadamente duas requisições por domínio.
# Mantém uma média prudente por domínio; acelerações específicas continuam
# configuradas por slot e não devem transformar a coleta em rajadas.
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

# Não mostra os cálculos internos do AutoThrottle nos logs normais.
AUTOTHROTTLE_DEBUG = False


# Tenta novamente requisições que falharam temporariamente.
RETRY_ENABLED = True

# Faz no máximo duas novas tentativas depois da primeira.
RETRY_TIMES = 2

# Códigos que normalmente representam falhas temporárias.
RETRY_HTTP_CODES = [
    408,
    429,
    500,
    502,
    503,
    504,
    522,
    524,
]

# Uma nova tentativa recebe prioridade um pouco menor.
RETRY_PRIORITY_ADJUST = -1


# Interrompe uma requisição que demorar mais de 20 segundos.
DOWNLOAD_TIMEOUT = 20

# A resolução DNS usa uma espera própria no resolver padrão do Scrapy.
DNS_TIMEOUT = 20

# Impede baixar uma resposta maior que 10 megabytes.
DOWNLOAD_MAXSIZE = 10 * 1024 * 1024

# Impede seguir cadeias enormes de redirecionamento.
REDIRECT_MAX_TIMES = 5

# Limita a profundidade de navegação durante o piloto.
DEPTH_LIMIT = 3

# Encerra o crawler piloto depois de receber 100 páginas.
#
# Esse limite será configurável por execução posteriormente.
CLOSESPIDER_PAGECOUNT = 100


# Idiomas preferidos para as respostas.
DEFAULT_REQUEST_HEADERS = {
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


# Mostra informações importantes sem poluir o terminal.
LOG_LEVEL = "INFO"

# Exibe estatísticas resumidas a cada 30 segundos.
LOGSTATS_INTERVAL = 30.0

# Garante que textos exportados utilizem UTF-8.
FEED_EXPORT_ENCODING = "utf-8"
