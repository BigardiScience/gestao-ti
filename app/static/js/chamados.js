/* Chamados (TI): contador no menu que se atualiza sozinho, aviso do Windows com som, número no título
   da aba e os detalhes do campo de resposta. O contador não renova a sessão: quem sai da frente do
   computador continua sendo desconectado por inatividade. */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const meta = (nome) => { const m = $('meta[name="' + nome + '"]'); return m ? m.content : null; };

  const urlContador = meta("chamados-contador");
  const urlFila = meta("chamados-fila");
  const tituloOriginal = document.title.replace(/^\(\d+\)\s*/, "");
  const CHAVE = "chamados:ultimo-visto";
  const CHAVE_RESPOSTA = "chamados:ultima-resposta";
  const INTERVALO = 10000;      // contador do menu e avisos (qualquer tela da TI)
  const INTERVALO_CONVERSA = 4000;  // chamado aberto: mensagens novas aparecem sozinhas
  let intervalo = null;
  const conversaViva = $("[data-conversa-ao-vivo]");

  // Repete "fn" a cada "ms". Usa um worker porque o navegador segura os temporizadores de abas em segundo
  // plano (até 1 vez por minuto). Devolve uma função que para o relógio.
  function relogio(ms, fn) {
    const url = meta("relogio");
    if (url && window.Worker) {
      try {
        const w = new Worker(url);
        w.onmessage = fn;
        w.postMessage(ms);
        return function () { w.terminate(); };
      } catch (e) { /* sem worker: usa o temporizador comum */ }
    }
    const t = setInterval(fn, ms);
    return function () { clearInterval(t); };
  }

  function guardar(chave, valor) { try { localStorage.setItem(chave, String(valor)); } catch (e) { /* modo privado */ } }
  function ler(chave) { try { return parseInt(localStorage.getItem(chave) || "0", 10) || 0; } catch (e) { return 0; } }

  function bip() {
    try {
      const Contexto = window.AudioContext || window.webkitAudioContext;
      if (!Contexto) return;
      const ctx = new Contexto();
      [0, 0.18].forEach(function (atraso) {
        const osc = ctx.createOscillator();
        const volume = ctx.createGain();
        osc.type = "sine";
        osc.frequency.value = 880;
        volume.gain.setValueAtTime(0.0001, ctx.currentTime + atraso);
        volume.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + atraso + 0.02);
        volume.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + atraso + 0.15);
        osc.connect(volume).connect(ctx.destination);
        osc.start(ctx.currentTime + atraso);
        osc.stop(ctx.currentTime + atraso + 0.16);
      });
      setTimeout(() => ctx.close(), 800);
    } catch (e) { /* o navegador bloqueou o som até a primeira interação: tudo bem */ }
  }

  function avisarWindows(novos) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try {
      const aviso = new Notification("Chamado novo na fila", {
        body: novos === 1 ? "1 chamado esperando alguém assumir." : novos + " chamados esperando alguém assumir.",
        tag: "chamados-novos", renotify: true,
      });
      aviso.onclick = function () { window.focus(); location.href = urlFila; aviso.close(); };
    } catch (e) { /* alguns navegadores só deixam notificar pelo service worker */ }
  }

  function lerTexto(chave) { try { return localStorage.getItem(chave) || ""; } catch (e) { return ""; } }

  function avisarResposta(resposta) {
    if (conversaViva && conversaViva.dataset.chamado === String(resposta.id)) return;  // a própria tela já mostra
    bip();
    const faixa = $('[data-resposta-nova][data-chamado="' + resposta.id + '"]');
    if (faixa) {
      // endereço sempre diferente do atual: o navegador recarrega a página em vez de só rolar até a âncora
      const atualizar = $("a", faixa);
      if (atualizar) atualizar.href = urlFila + "/" + resposta.id + "?nova=" + Date.now() + "#nova";
      faixa.hidden = false;
    }
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try {
      const aviso = new Notification(resposta.nome + " respondeu o chamado #" + resposta.numero, {
        body: "Clique para abrir a conversa.", tag: "chamados-resposta-" + resposta.id, renotify: true,
      });
      aviso.onclick = function () { window.focus(); location.href = urlFila + "/" + resposta.id + "#conversa"; aviso.close(); };
    } catch (e) { /* alguns navegadores só deixam notificar pelo service worker */ }
  }

  function aplicar(dados) {
    const novos = dados.novos || 0;
    const total = novos + (dados.respostas || 0);
    $$("[data-contador-chamados]").forEach(function (selo) {
      selo.textContent = total;
      selo.hidden = !total;
    });
    document.title = total ? "(" + total + ") " + tituloOriginal : tituloOriginal;
    // Resposta de colaborador mais nova do que a última que este navegador já avisou.
    // Na primeira vez (navegador sem nada guardado) só marca o ponto de partida: o aviso vale dali em diante,
    // inclusive para a primeira resposta de todas.
    const vista = lerTexto(CHAVE_RESPOSTA);
    if (!vista) {
      guardar(CHAVE_RESPOSTA, dados.ultima_resposta || "0");
    } else if (dados.ultima_resposta && dados.ultima_resposta > vista) {
      if (dados.resposta) avisarResposta(dados.resposta);
      guardar(CHAVE_RESPOSTA, dados.ultima_resposta);
    }
    // Chamado novo na fila (mesma regra)
    if (!lerTexto(CHAVE)) {
      guardar(CHAVE, dados.ultimo || 0);
    } else if (dados.ultimo && dados.ultimo > ler(CHAVE)) {
      bip();
      avisarWindows(novos);
      const faixa = $("[data-fila-novidade]");
      if (faixa) faixa.hidden = false;
      guardar(CHAVE, dados.ultimo);
    }
  }

  function consultar() {
    if (!urlContador) return;
    fetch(urlContador, { headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (r.status === 401 || r.status === 403 || r.redirected) { sessaoPerdida(); return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (dados) { if (dados && typeof dados.novos === "number") aplicar(dados); })
      .catch(function () { /* sem rede: tenta de novo no próximo ciclo */ });
  }

  function parar() { if (intervalo) intervalo(); intervalo = null; if (pararConversa) pararConversa(); pararConversa = null; }
  let pararConversa = null;

  // A aba saiu do sistema (sessão expirou, alguém clicou em "Sair" ou entrou com outro usuário neste
  // navegador): os avisos param. Mostra isso na tela, em vez de parar em silêncio.
  function sessaoPerdida() {
    parar();
    if ($("[data-sessao-perdida]")) return;
    const caixa = document.createElement("div");
    caixa.className = "aviso aviso--aviso aviso-sessao";
    caixa.setAttribute("role", "alert");
    caixa.setAttribute("data-sessao-perdida", "");
    const texto = document.createElement("div");
    texto.className = "aviso__texto";
    const titulo = document.createElement("strong");
    titulo.textContent = "Os avisos de chamados pararam nesta aba.";
    const explica = document.createElement("span");
    explica.textContent = " A sessão terminou ou outra pessoa entrou neste navegador. ";
    const link = document.createElement("a");
    link.href = urlFila || "/";
    link.textContent = "Entrar de novo";
    texto.append(titulo, explica, link);
    caixa.appendChild(texto);
    document.body.appendChild(caixa);
    document.title = "(sem avisos) " + tituloOriginal;
  }

  if (urlContador) {
    consultar();
    intervalo = relogio(INTERVALO, consultar);
    document.addEventListener("visibilitychange", function () { if (!document.hidden && intervalo) consultar(); });
  }

  function avisoDeTeste() {
    bip();
    try {
      new Notification("Teste de aviso dos chamados", {
        body: "Se você está vendo isto, os chamados novos e as respostas vão aparecer aqui.", tag: "chamados-teste",
      });
    } catch (e) { /* alguns navegadores só deixam notificar pelo service worker */ }
  }

  // Linha da fila que diz se os avisos do Windows estão ligados neste navegador (e deixa testar)
  const estado = $("[data-avisos-estado]");
  function mostrarEstado() {
    if (!estado) return;
    estado.textContent = "";
    estado.hidden = false;
    if (!("Notification" in window)) {
      estado.textContent = "Avisos do Windows indisponíveis neste endereço: o navegador só permite com HTTPS.";
    } else if (Notification.permission === "granted") {
      estado.append("Avisos do Windows ligados neste navegador. ");
      const testar = document.createElement("button");
      testar.type = "button";
      testar.className = "link-botao";
      testar.textContent = "Testar aviso";
      testar.addEventListener("click", function () {
        avisoDeTeste();
        let dica = $("[data-avisos-dica]", estado);
        if (!dica) {
          dica = document.createElement("span");
          dica.setAttribute("data-avisos-dica", "");
          estado.append(" ", dica);
        }
        dica.textContent = "Enviado. Se não apareceu no canto da tela, confira em Configurações do Windows › Sistema › " +
          "Notificações se o navegador está ligado e se o “Não perturbe” está desligado.";
      });
      estado.appendChild(testar);
    } else if (Notification.permission === "denied") {
      estado.textContent = "Avisos do Windows bloqueados neste navegador. Para liberar: clique no ícone à esquerda " +
        "do endereço do site › Notificações › Permitir e recarregue a página.";
    } else {
      estado.hidden = true;  // ainda não perguntou: o botão "Ativar avisos do Windows" aparece no alto
    }
  }

  // botão "Ativar avisos do Windows" (o navegador só pergunta depois de um clique)
  const ativar = $("[data-ativar-avisos]");
  if (ativar && "Notification" in window && Notification.permission === "default") {
    ativar.hidden = false;
    ativar.addEventListener("click", function () {
      Notification.requestPermission().then(function (resposta) {
        ativar.hidden = true;
        if (resposta === "granted") avisoDeTeste();
        mostrarEstado();
      });
    });
  }
  mostrarEstado();

  // campo de resposta: troca entre "Responder" e "Nota interna"
  $$("[data-resposta]").forEach(function (form) {
    const texto = $("[data-resposta-texto]", form);
    function atualizar() {
      const tipo = ($('input[name="tipo"]:checked', form) || {}).value;
      form.classList.toggle("resposta--nota", tipo === "nota");
      if (texto) texto.placeholder = tipo === "nota" ? texto.dataset.placeholderNota : texto.dataset.placeholderResposta;
    }
    form.addEventListener("change", atualizar);
    atualizar();
    const anexo = $("[data-anexo]", form);
    const nome = $("[data-anexo-nome]", form);
    if (anexo && nome) {
      anexo.addEventListener("change", function () {
        nome.textContent = anexo.files && anexo.files[0] ? anexo.files[0].name : "";
      });
    }
    // o envio duplo (clique duplo) já é barrado pelo app.js, para todos os formulários
  });

  // Conversa do chamado em tempo real: as mensagens novas entram na tela, sem recarregar e sem perder o texto
  // que a pessoa está digitando. A resposta do colaborador toca o som (e avisa no Windows se a aba está escondida).
  if (conversaViva) {
    let ultima = parseInt(conversaViva.dataset.ultima, 10) || 0;
    const situacaoNaTela = conversaViva.dataset.situacao;
    let buscando = false;
    const buscar = function () {
      if (buscando) return;
      buscando = true;
      fetch(conversaViva.dataset.url + "?depois=" + ultima, { headers: { Accept: "application/json" },
        credentials: "same-origin", cache: "no-store" })
        .then(function (r) {
          if (r.status === 401 || r.status === 403 || r.redirected) { sessaoPerdida(); return null; }
          return r.ok ? r.json() : null;
        })
        .then(function (d) {
          if (!d) return;
          if (d.html && d.ultima > ultima) {
            conversaViva.insertAdjacentHTML("beforeend", d.html);
            ultima = d.ultima;
            if (d.respondeu) {
              bip();
              if (d.resposta_em && d.resposta_em > lerTexto(CHAVE_RESPOSTA)) guardar(CHAVE_RESPOSTA, d.resposta_em);
              if (document.hidden && "Notification" in window && Notification.permission === "granted") {
                try { new Notification(d.nome + " respondeu o chamado #" + d.numero, { tag: "chamados-resposta-" + conversaViva.dataset.chamado }); } catch (e) { /* ok */ }
              }
              const nova = conversaViva.lastElementChild;
              if (nova && !document.hidden) nova.scrollIntoView({ block: "nearest", behavior: "smooth" });
            }
          }
          const faixa = $("[data-situacao-mudou]");
          if (faixa && d.situacao !== situacaoNaTela) faixa.hidden = false;
        })
        .catch(function () { /* sem rede: tenta de novo */ })
        .finally(function () { buscando = false; });
    };
    pararConversa = relogio(INTERVALO_CONVERSA, buscar);
    document.addEventListener("visibilitychange", function () { if (!document.hidden && pararConversa) buscar(); });
  }

  // painel "Atendimento": o botão "Salvar alterações" só aparece quando algo mudou;
  // marcar como resolvido por aqui pede a mesma confirmação do botão verde
  $$("[data-atendimento]").forEach(function (form) {
    const salvar = $("[data-salvar-atendimento]", form);
    const campos = $$("select", form);
    const originais = campos.map((c) => c.value);
    const situacao = $('select[name="situacao"]', form);
    function atualizar() {
      const mudou = campos.some((c, i) => c.value !== originais[i]);
      if (salvar) salvar.hidden = !mudou;
      if (situacao && situacao.value === "resolvido" && originais[campos.indexOf(situacao)] !== "resolvido") {
        form.dataset.confirmar = form.dataset.confirmarResolver;
        form.dataset.confirmarTitulo = "Marcar como resolvido?";
        form.dataset.confirmarBotao = "Marcar como resolvido";
      } else {
        delete form.dataset.confirmar;
      }
    }
    form.addEventListener("change", atualizar);
    atualizar();
  });
})();
