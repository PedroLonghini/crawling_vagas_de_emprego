"""Teste real do Chromium com HTML local interceptado, sem consultar sites."""

from scrapy.http import HtmlResponse

from observatorio_vagas.crawling.adaptadores.generico import AdaptadorGenericoHTML
from observatorio_vagas.crawling.javascript import renderizar_em_thread


def main():
    url = "https://empresa.example/carreiras"
    html = """<html><body><div id="app"></div><script>
    setTimeout(() => {
        [1, 2].forEach(id => {
          const link = document.createElement('a');
          link.href = '/va' + 'ga/' + id;
          link.textContent = 'Cargo ' + id;
          document.querySelector('#app').appendChild(link);
        });
    }, 100);
    </script></body></html>"""
    adaptador = AdaptadorGenericoHTML()
    antes = adaptador.descobrir(HtmlResponse(url, body=html.encode(), encoding="utf-8"))
    assert not antes
    corpo = renderizar_em_thread(
        url,
        html,
        "User-agent: *\nAllow: /",
        "ObservatorioVagas/0.1.0",
        30,
        1,
        1024 * 1024,
        0.1,
    )
    depois = adaptador.descobrir(HtmlResponse(url, body=corpo, encoding="utf-8"))
    assert {c.url for c in depois} == {f"https://empresa.example/vaga/{i}" for i in (1, 2)}
    print(f"Antes: {len(antes)} candidatos; depois: {len(depois)} candidatos.")
    print("Chromium executou o JavaScript e o adaptador leu as duas vagas.")
    dinamico = """<html><body style="min-height:2400px"><div id="cards"></div>
    <button type="button" id="mais">Carregar mais vagas</button><script>
    function adicionar(id) {
      const link = document.createElement('a');
      link.href = '/va' + 'ga/' + id;
      link.textContent = 'Cargo ' + id;
      document.querySelector('#cards').appendChild(link);
    }
    setTimeout(() => { adicionar(1); adicionar(2); }, 100);
    document.querySelector('#mais').onclick = () => {
      document.querySelector('#cards').replaceChildren();
      adicionar(3);
      document.querySelector('#mais').disabled = true;
    };
    let rolou = false;
    window.addEventListener('scroll', () => {
      if (!rolou) {
        rolou = true; document.querySelector('#cards').replaceChildren(); adicionar(4);
      }
    });
    </script></body></html>"""
    corpo = renderizar_em_thread(
        url,
        dinamico,
        "User-agent: *\nAllow: /",
        "ObservatorioVagas/0.1.0",
        30,
        0.2,
        1024 * 1024,
        0.1,
        5,
    )
    descobertas = adaptador.descobrir(HtmlResponse(url, body=corpo, encoding="utf-8"))
    assert {c.url for c in descobertas} == {
        f"https://empresa.example/vaga/{i}" for i in (1, 2, 3, 4)
    }
    print("Botão + rolagem + preservação de cards removidos: 4 vagas recuperadas.")
    interno = """<html><body>
    <div id="painel" style="height:200px;overflow-y:auto">
      <div style="height:1000px" id="cards"></div>
    </div>
    <script src="https://cdn.example/biblioteca.js"></script>
    <script>
      function adicionar(id) {
        const a = document.createElement('a'); a.href = '/va' + 'ga/' + id;
        a.textContent = 'Cargo ' + id; document.querySelector('#cards').appendChild(a);
      }
      setTimeout(() => adicionar(1), 100);
      document.querySelector('#painel').addEventListener('scroll', () => adicionar(9), {once:true});
    </script></body></html>"""
    diagnostico = {}
    corpo = renderizar_em_thread(
        url,
        interno,
        "User-agent: *\nAllow: /",
        "ObservatorioVagas/0.1.0",
        30,
        0.2,
        1024 * 1024,
        0.1,
        5,
        diagnostico,
    )
    descobertas = adaptador.descobrir(HtmlResponse(url, body=corpo, encoding="utf-8"))
    assert {c.url for c in descobertas} == {f"https://empresa.example/vaga/{i}" for i in (1, 9)}
    assert diagnostico["movimentos_rolagem"] > 0
    assert diagnostico["dominios_externos_bloqueados"] == {"cdn.example": 1}
    print("Rolagem interna: 2 vagas; dependência externa identificada no diagnóstico.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
