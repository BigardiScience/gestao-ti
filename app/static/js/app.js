/* Comportamentos da interface. Sem dependências externas. Precisam de JavaScript: o desenho da
   assinatura e a busca nos campos de escolha de colaborador e aparelho. */
(function () {
  "use strict";

  const $ = (seletor, raiz) => (raiz || document).querySelector(seletor);
  const $$ = (seletor, raiz) => Array.from((raiz || document).querySelectorAll(seletor));

  /* ---------------------------------------------------------------- menu lateral no celular */
  const lateral = $("#menu-lateral");
  const botaoMenu = $("[data-abrir-menu]");
  let veu = null;
  function fecharMenu() {
    if (!lateral) return;
    lateral.classList.remove("lateral--aberta");
    botaoMenu && botaoMenu.setAttribute("aria-expanded", "false");
    if (veu) { veu.remove(); veu = null; }
  }
  if (botaoMenu && lateral) {
    botaoMenu.addEventListener("click", function () {
      const aberto = lateral.classList.toggle("lateral--aberta");
      botaoMenu.setAttribute("aria-expanded", aberto ? "true" : "false");
      if (aberto) {
        veu = document.createElement("div");
        veu.className = "veu";
        veu.addEventListener("click", fecharMenu);
        document.body.appendChild(veu);
        const primeiro = $("a", lateral);
        primeiro && primeiro.focus();
      } else {
        fecharMenu();
      }
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") fecharMenu(); });
  }

  /* ---------------------------------------------------------------- avisos que podem ser fechados */
  document.addEventListener("click", function (e) {
    const fechar = e.target.closest("[data-fechar-aviso]");
    if (fechar) fechar.closest(".aviso").remove();
  });

  /* ---------------------------------------------------------------- máscaras de digitação */
  const mascaras = {
    cpf: (d) => d.slice(0, 11).replace(/(\d{3})(\d)/, "$1.$2").replace(/(\d{3})\.(\d{3})(\d)/, "$1.$2.$3")
      .replace(/(\d{3})\.(\d{3})\.(\d{3})(\d)/, "$1.$2.$3-$4"),
    telefone: (d) => {
      d = d.slice(0, 11);
      if (d.length <= 2) return d.length ? "(" + d : "";
      if (d.length <= 6) return "(" + d.slice(0, 2) + ") " + d.slice(2);
      if (d.length <= 10) return "(" + d.slice(0, 2) + ") " + d.slice(2, 6) + "-" + d.slice(6);
      return "(" + d.slice(0, 2) + ") " + d.slice(2, 7) + "-" + d.slice(7);
    },
    imei: (d) => d.slice(0, 15),
    codigo: (d) => d.slice(0, 6),
  };
  $$("[data-mascara]").forEach(function (campo) {
    const tipo = campo.dataset.mascara;
    const aplicar = function () {
      if (tipo === "cnpj") return;
      const digitos = campo.value.replace(/\D/g, "");
      const formatado = mascaras[tipo] ? mascaras[tipo](digitos) : digitos;
      if (campo.value !== formatado) campo.value = formatado;
    };
    campo.addEventListener("input", aplicar);
    aplicar();
  });

  /* ---------------------------------------------------------------- campo de escolha com busca no servidor */
  $$("[data-seletor]").forEach(function (caixa) {
    const url = caixa.dataset.seletor;
    const valor = $("[data-seletor-valor]", caixa);
    const busca = $('input[type="search"]', caixa);
    const lista = $(".seletor__lista", caixa);
    const escolhido = $(".seletor__escolhido", caixa);
    const texto = $("[data-seletor-texto]", caixa);
    let itens = [], ativo = -1, espera = null, pedido = 0, pendente = false, escolherAoChegar = false;

    function marcarAtivo() {
      $$(".seletor__opcao", lista).forEach(function (botao, i) {
        botao.setAttribute("aria-selected", i === ativo ? "true" : "false");
        if (i === ativo) botao.scrollIntoView({ block: "nearest" });
      });
      if (ativo >= 0) busca.setAttribute("aria-activedescendant", lista.id + "-op-" + ativo);
      else busca.removeAttribute("aria-activedescendant");
    }
    function desenhar() {
      lista.textContent = "";
      if (!itens.length) {
        const vazio = document.createElement("li");
        vazio.className = "seletor__vazio";
        vazio.textContent = busca.value.trim() ? "Nada encontrado." : "Nenhuma opção disponível.";
        lista.appendChild(vazio);
      }
      itens.forEach(function (item, i) {
        const li = document.createElement("li");
        const botao = document.createElement("button");
        botao.type = "button";
        botao.className = "seletor__opcao";
        botao.id = lista.id + "-op-" + i;
        botao.setAttribute("role", "option");
        botao.tabIndex = -1;
        botao.append(item.titulo);
        const detalhe = document.createElement("small");
        detalhe.textContent = item.detalhe || "";
        botao.appendChild(detalhe);
        botao.addEventListener("mousedown", (e) => e.preventDefault());
        botao.addEventListener("click", () => escolher(item));
        li.appendChild(botao);
        lista.appendChild(li);
      });
      marcarAtivo();
      busca.setAttribute("aria-expanded", "true");
    }
    function carregar() {
      const numero = ++pedido;
      const consulta = busca.value.trim();
      pendente = true;
      const endereco = url + (url.indexOf("?") === -1 ? "?" : "&") + "q=" + encodeURIComponent(consulta);
      fetch(endereco, { headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then((r) => (r.ok ? r.json() : []))
        .then(function (dados) {
          if (numero !== pedido) return;
          // resposta de uma busca antiga (a pessoa já digitou mais): mostra, mas não escolhe por ela
          const atual = consulta === busca.value.trim();
          if (atual) pendente = false;
          itens = dados;
          ativo = dados.length ? 0 : -1;
          if (escolherAoChegar && atual) {
            escolherAoChegar = false;
            if (itens.length) return escolher(itens[0]);
          }
          desenhar();
        })
        .catch(function () { pendente = escolherAoChegar = false; /* sem conexão: mantém a lista anterior */ });
    }
    function fechar() {
      lista.textContent = "";
      busca.setAttribute("aria-expanded", "false");
      busca.removeAttribute("aria-activedescendant");
    }
    function escolher(item) {
      valor.value = item.id;
      texto.textContent = item.titulo;
      const detalhe = document.createElement("small");
      detalhe.textContent = item.detalhe || "";
      texto.appendChild(detalhe);
      escolhido.hidden = false;
      busca.hidden = true;
      fechar();
      valor.dispatchEvent(new Event("change", { bubbles: true }));
      const botaoTrocar = $("[data-seletor-trocar]", caixa);
      botaoTrocar && botaoTrocar.focus();
    }
    busca.addEventListener("input", function () {
      // a lista na tela ficou velha: até a resposta chegar, Enter espera por ela em vez de escolher a errada
      pendente = true;
      ativo = -1;
      marcarAtivo();
      clearTimeout(espera);
      espera = setTimeout(carregar, 160);
    });
    busca.addEventListener("focus", carregar);
    busca.addEventListener("blur", () => setTimeout(fechar, 150));
    busca.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!itens.length || pendente) return;
        ativo = (ativo + (e.key === "ArrowDown" ? 1 : -1) + itens.length) % itens.length;
        marcarAtivo();
      } else if (e.key === "Enter") {
        e.preventDefault();  // Enter escolhe a opção em vez de enviar o formulário
        if (pendente) escolherAoChegar = true;
        else if (ativo >= 0 && itens[ativo]) escolher(itens[ativo]);
      } else if (e.key === "Escape") {
        fechar();
      }
    });
    $("[data-seletor-trocar]", caixa).addEventListener("click", function () {
      valor.value = "";
      valor.dispatchEvent(new Event("change", { bubbles: true }));
      escolhido.hidden = true;
      busca.hidden = false;
      busca.value = "";
      busca.focus();
    });
  });

  /* ---------------------------------------------------------------- menus suspensos (mais ações, filtros) */
  document.addEventListener("click", function (e) {
    $$("details.acoes-mais[open], details.filtros-mais[open]").forEach(function (menu) {
      if (!menu.contains(e.target)) menu.open = false;
    });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    $$("details.acoes-mais[open], details.filtros-mais[open]").forEach(function (menu) {
      menu.open = false;
      const resumo = $("summary", menu);
      resumo && resumo.focus();
    });
  });

  /* ---------------------------------------------------------------- mostrar/ocultar conforme outro campo */
  function atualizarDependentes() {
    $$("[data-mostrar-quando]").forEach(function (bloco) {
      const [nome, valores] = bloco.dataset.mostrarQuando.split("=");
      const aceitos = valores.split("|");
      const campos = $$(`[name="${nome}"]`);
      let valor = "";
      campos.forEach(function (c) {
        if ((c.type === "radio" || c.type === "checkbox")) { if (c.checked) valor = c.value; }
        else valor = c.value;
      });
      const mostrar = valores === "*" ? valor !== "" : aceitos.indexOf(valor) !== -1;
      bloco.hidden = !mostrar;
      $$("input, select, textarea", bloco).forEach((c) => { if (c.dataset.obrigatorioQuandoVisivel) c.required = mostrar; });
    });
  }
  document.addEventListener("change", atualizarDependentes);
  atualizarDependentes();

  /* ---------------------------------------------------------------- confirmação antes de ações sensíveis */
  const dialogo = $("#dialogo-confirmar");
  let formularioPendente = null;
  document.addEventListener("submit", function (e) {
    const form = e.target;
    if (form.dataset.enviando === "1") { e.preventDefault(); return; }
    if (form.dataset.confirmar && !form.dataset.confirmado && dialogo && dialogo.showModal) {
      e.preventDefault();
      formularioPendente = form;
      $("[data-dialogo-titulo]", dialogo).textContent = form.dataset.confirmarTitulo || "Confirmar ação";
      $("[data-dialogo-texto]", dialogo).textContent = form.dataset.confirmar;
      const botao = $("[data-dialogo-ok]", dialogo);
      botao.textContent = form.dataset.confirmarBotao || "Confirmar";
      botao.className = "botao " + (form.dataset.confirmarPerigo ? "botao--perigo" : "botao--primario");
      const campoExtra = $("[data-dialogo-campo]", dialogo);
      const entrada = $("textarea", campoExtra);
      if (form.dataset.confirmarCampo) {
        campoExtra.hidden = false;
        $("label", campoExtra).textContent = form.dataset.confirmarCampoRotulo || "Motivo";
        entrada.value = "";
        entrada.required = true;
      } else {
        campoExtra.hidden = true;
        entrada.required = false;
      }
      dialogo.showModal();
      (form.dataset.confirmarCampo ? entrada : botao).focus();
      return;
    }
    if (!e.defaultPrevented) {
      form.dataset.enviando = "1";
      form.setAttribute("aria-busy", "true");
      setTimeout(function () { delete form.dataset.enviando; form.removeAttribute("aria-busy"); }, 8000);
    }
  });
  if (dialogo) {
    $("[data-dialogo-cancelar]", dialogo).addEventListener("click", () => { dialogo.close(); formularioPendente = null; });
    $("form", dialogo).addEventListener("submit", function (e) {
      e.preventDefault();
      if (!formularioPendente) return dialogo.close();
      const nomeCampo = formularioPendente.dataset.confirmarCampo;
      if (nomeCampo) {
        const valor = $("textarea", dialogo).value.trim();
        if (!valor) { $("textarea", dialogo).focus(); return; }
        let oculto = formularioPendente.querySelector(`input[name="${nomeCampo}"]`);
        if (!oculto) {
          oculto = document.createElement("input");
          oculto.type = "hidden";
          oculto.name = nomeCampo;
          formularioPendente.appendChild(oculto);
        }
        oculto.value = valor;
      }
      formularioPendente.dataset.confirmado = "1";
      dialogo.close();
      const alvo = formularioPendente;
      formularioPendente = null;
      if (alvo.requestSubmit) alvo.requestSubmit(); else alvo.submit();
    });
  }

  /* ---------------------------------------------------------------- copiar para a área de transferência */
  document.addEventListener("click", function (e) {
    const botao = e.target.closest("[data-copiar]");
    if (!botao || !navigator.clipboard) return;
    navigator.clipboard.writeText(botao.dataset.copiar).then(function () {
      const original = botao.textContent;
      botao.textContent = "Copiado";
      setTimeout(() => { botao.textContent = original; }, 1600);
    });
  });

  /* ---------------------------------------------------------------- relatórios */
  $$("[data-marcar-colunas]").forEach(function (botao) {
    botao.addEventListener("click", function () {
      const marcar = botao.dataset.marcarColunas === "todas";
      $$('input[name="colunas"]').forEach((c) => { c.checked = marcar; });
    });
  });
  $$("[data-enviar-ao-mudar]").forEach(function (campo) {
    campo.addEventListener("change", function () {
      const form = campo.form;
      $$('input[name="colunas"]', form).forEach((c) => { c.disabled = true; });
      $$('[name="previa"]', form).forEach((c) => { c.disabled = true; });
      form.submit();
    });
  });

  /* ---------------------------------------------------------------- envio do formulário ao trocar filtro */
  $$("select[data-filtro-automatico]").forEach(function (campo) {
    campo.addEventListener("change", () => campo.form.submit());
  });
})();
