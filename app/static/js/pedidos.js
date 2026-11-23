/* Pedidos para a TI — assistente de 3 passos.
   Sem JavaScript tudo funciona (cada botão envia o formulário e o servidor mostra o passo seguinte).
   Com JavaScript a troca de passo é instantânea, o texto digitado nunca se perde e o botão
   "voltar" do navegador volta um passo. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  // evita envio duplo (clique duplo ou internet lenta)
  $$("form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      const botao = e.submitter;
      if (!botao || botao.formNoValidate) return;
      if (form.dataset.enviando) { e.preventDefault(); return; }
      form.dataset.enviando = "1";
      setTimeout(function () { botao.disabled = true; botao.dataset.texto = botao.textContent; }, 0);
    });
  });

  // mostra o nome da foto escolhida no botão "Anexar foto ou print"
  $$("[data-anexo]").forEach(function (entrada) {
    const botao = document.querySelector('label[for="' + entrada.id + '"]');
    const nome = botao && $("[data-anexo-nome]", botao);
    if (!nome) return;
    const original = nome.textContent;
    entrada.addEventListener("change", function () {
      const arquivo = entrada.files && entrada.files[0];
      nome.textContent = arquivo ? "Foto escolhida: " + arquivo.name : original;
      botao.classList.toggle("anexo__botao--escolhido", !!arquivo);
    });
  });

  // Relógio que não é segurado pelo navegador quando a aba está em segundo plano (worker); senão, setInterval.
  function relogio(ms, fn) {
    const url = (document.querySelector('meta[name="relogio"]') || {}).content;
    if (url && window.Worker) {
      try { const w = new Worker(url); w.onmessage = fn; w.postMessage(ms); return function () { w.terminate(); }; }
      catch (e) { /* segue com o temporizador comum */ }
    }
    const t = setInterval(fn, ms);
    return function () { clearInterval(t); };
  }

  // Selos de respostas novas da TI: no botão "Meus pedidos" e em cada pedido da lista
  const principal = $("[data-nao-lidas]");
  if (principal && principal.dataset.naoLidas) {
    const frase = (n) => n + (n === 1 ? " resposta nova" : " respostas novas") + " da TI";
    const atualizarSelos = function (d) {
      const total = $("[data-novas-total]");
      if (total) { total.textContent = d.total; total.hidden = !d.total; }
      const texto = $("[data-novas-total-texto]");
      if (texto) texto.textContent = d.total ? "(" + frase(d.total) + ")" : "";
      $$("[data-novas-pedido]").forEach(function (selo) {
        const n = d.pedidos[selo.dataset.novasPedido] || 0;
        selo.hidden = !n;
        selo.title = frase(n);
        selo.firstChild.nodeValue = String(n);
        const oculto = selo.querySelector(".visualmente-oculto");
        if (oculto) oculto.textContent = " " + frase(n).replace(/^\d+ /, "");
      });
    };
    let pararSelos = null;
    let anterior = null;
    const buscarSelos = function () {
      fetch(principal.dataset.naoLidas, { headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" })
        .then(function (r) {
          if (r.status === 401 || r.status === 403 || r.redirected) { if (pararSelos) pararSelos(); return null; }
          return r.ok ? r.json() : null;
        })
        .then(function (d) {
          if (!d || typeof d.total !== "number") return;
          // Na lista "Meus pedidos" (sem campos para digitar), qualquer mudança recarrega a página: assim a
          // situação de cada pedido e a ordem também ficam certas.
          const lista = $(".pedidos");
          const agora = JSON.stringify(d.pedidos);
          if (lista && anterior !== null && agora !== anterior) { location.reload(); return; }
          anterior = agora;
          atualizarSelos(d);
        })
        .catch(function () { /* sem rede: tenta de novo */ });
    };
    buscarSelos();  // ponto de partida
    pararSelos = relogio(10000, buscarSelos);
  }

  // Conversa do pedido em tempo real: a resposta da TI aparece sozinha, sem perder o que a pessoa digita.
  const conversa = $("[data-conversa-ao-vivo]");
  if (conversa) {
    let ultima = parseInt(conversa.dataset.ultima, 10) || 0;
    const titulo = document.title;
    let parar = null;
    const buscar = function () {
      fetch(conversa.dataset.url + "?depois=" + ultima, { headers: { Accept: "application/json" },
        credentials: "same-origin", cache: "no-store" })
        .then(function (r) {
          if (r.status === 401 || r.status === 403 || r.redirected) { if (parar) parar(); return null; }
          return r.ok ? r.json() : null;
        })
        .then(function (d) {
          if (!d) return;
          if (d.html && d.ultima > ultima) {
            conversa.insertAdjacentHTML("beforeend", d.html);
            ultima = d.ultima;
            if (d.respondeu && document.hidden) document.title = "(1) Resposta da TI | " + titulo;
          }
          const faixa = $("[data-situacao-mudou]");
          if (faixa && d.situacao !== conversa.dataset.situacao) faixa.hidden = false;
        })
        .catch(function () { /* sem rede: tenta de novo */ });
    };
    parar = relogio(4000, buscar);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) { document.title = titulo; buscar(); } });
  }

  const form = $("[data-assistente]");
  if (!form) return;

  const campoCategoria = $("[data-campo-categoria]", form);
  const campoEquipamento = $("[data-campo-equipamento]", form);
  const texto = $("#descricao", form);
  const voltar = $("[data-voltar]", form);
  const comEquipamento = (form.dataset.comEquipamento || "").split(" ");
  let atual = parseInt(form.dataset.passo || "1", 10);

  // São sempre 3 passos. O passo 2 (qual equipamento) só existe para Computador e Celular;
  // nas outras categorias o assistente vai do passo 1 direto para o 3 ("Passo 3 de 3").
  function temPasso2() { return comEquipamento.indexOf(campoCategoria.value) !== -1; }

  function mostrar(passo, empilhar) {
    if (!campoCategoria.value) passo = 1;
    else if (passo === 2 && !temPasso2()) passo = 3;
    atual = passo;
    $$("section[data-passo]", form).forEach((s) => { s.hidden = parseInt(s.dataset.passo, 10) !== passo; });
    const cat = campoCategoria.value;
    $$("[data-para]", form).forEach((bloco) => { bloco.hidden = bloco.dataset.para !== cat; });
    $("[data-passos-texto]", form).textContent = "Passo " + passo + " de 3";
    $$("[data-passos-barra] span", form).forEach((s, i) => s.classList.toggle("feito", i < passo));
    voltar.hidden = passo === 1;
    voltar.value = passo === 3 && temPasso2() ? "2" : "1";
    $("[data-padrao]", form).value = String(passo);
    if (empilhar) {
      const nivel = (history.state && history.state.nivel) || 0;
      history.pushState({ passo: passo, nivel: nivel + 1 }, "", location.href);
    }
    window.scrollTo(0, 0);
    const secao = $('section[data-passo="' + passo + '"]', form);
    const titulo = Array.from(secao.querySelectorAll("h1")).find((h) => h.offsetParent !== null);
    if (titulo) titulo.focus({ preventScroll: true });
  }

  function abrirLista(botao, focar) {
    const lista = $("[data-lista-computadores]", form);
    lista.hidden = false;
    botao.setAttribute("aria-expanded", "true");
    if (focar) {
      const primeiro = $("button", lista);
      if (primeiro) { primeiro.scrollIntoView({ block: "nearest" }); primeiro.focus({ preventScroll: true }); }
    }
  }

  function escolherCategoria(cat, botao) {
    const anterior = campoCategoria.value;
    campoCategoria.value = cat;
    if (anterior !== cat) {
      campoEquipamento.value = "";
      $$("[data-escolher-equipamento]", form).forEach((b) => b.setAttribute("aria-pressed", "false"));
    }
    $$("[data-escolher-categoria]", form).forEach((b) => b.setAttribute("aria-pressed", b === botao ? "true" : "false"));
    if (texto && botao.dataset.exemplo) texto.placeholder = botao.dataset.exemplo;
    const seguranca = cat === "seguranca";
    $("[data-aviso-seguranca]", form).hidden = !seguranca;
    $("[data-impacto]", form).hidden = seguranca;
    mostrar(2, true);
  }

  form.addEventListener("click", function (e) {
    if (e.target.closest("[data-padrao]")) { e.preventDefault(); return; }

    const categoria = e.target.closest("[data-escolher-categoria]");
    if (categoria) { e.preventDefault(); escolherCategoria(categoria.dataset.escolherCategoria, categoria); return; }

    const outro = e.target.closest("[data-abrir-lista]");
    if (outro) { e.preventDefault(); abrirLista(outro, true); return; }

    const equipamento = e.target.closest("[data-escolher-equipamento]");
    if (equipamento) {
      e.preventDefault();
      campoEquipamento.value = equipamento.dataset.escolherEquipamento;
      $$("[data-escolher-equipamento]", form).forEach((b) => b.setAttribute("aria-pressed", b === equipamento ? "true" : "false"));
      mostrar(3, true);
      return;
    }

    if (e.target.closest("[data-voltar]")) {
      e.preventDefault();
      // volta pelo histórico só o que este assistente empilhou (nunca sai da página sem querer)
      if (history.state && history.state.nivel > 0) history.back();
      else mostrar(parseInt(voltar.value, 10), false);
    }
  });

  // Enter num campo de uma linha não envia o formulário pela metade
  form.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.matches("input:not([type=submit]):not([type=button])")) e.preventDefault();
  });

  window.addEventListener("popstate", function (e) {
    mostrar(e.state && e.state.passo ? e.state.passo : 1, false);
  });

  // estado inicial (inclusive quando o servidor devolve a página com um aviso)
  history.replaceState({ passo: atual, nivel: 0 }, "", location.href);
  if (texto && campoCategoria.value) {
    const botao = $('[data-escolher-categoria="' + campoCategoria.value + '"]', form);
    if (botao && !texto.placeholder) texto.placeholder = botao.dataset.exemplo;
  }

  // antes de enviar: confere os campos do passo 3 sem perder nada
  form.addEventListener("submit", function (e) {
    if (!e.submitter || !e.submitter.hasAttribute("data-enviar")) return;
    const falta = [];
    if (!texto.value.trim() || texto.value.trim().length < 5) falta.push(texto);
    const impacto = $("[data-impacto]", form);
    if (!impacto.hidden && !$('input[name="impacto"]:checked', form)) falta.push($('input[name="impacto"]', form));
    if (falta.length) {
      e.preventDefault();
      delete form.dataset.enviando;
      setTimeout(() => { e.submitter.disabled = false; }, 0);
      mostrar(3, false);
      let aviso = $("[data-aviso-falta]", form);
      if (!aviso) {
        aviso = document.createElement("p");
        aviso.className = "ped-erro";
        aviso.setAttribute("role", "alert");
        aviso.setAttribute("data-aviso-falta", "");
        const linha = e.submitter.closest(".relato__enviar") || e.submitter;
        linha.parentNode.insertBefore(aviso, linha);
      }
      aviso.textContent = falta[0] === texto ? "Conte com algumas palavras o que aconteceu."
        : "Responda se isso está impedindo o seu trabalho.";
      falta[0].focus();
    }
  });
})();
