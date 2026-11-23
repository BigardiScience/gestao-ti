/* Quadro de assinatura: registra os traços (pontos x,y) feitos com dedo, caneta ou mouse.
   O servidor recebe os traços em JSON e os desenha no PDF em alta resolução. */
(function () {
  "use strict";
  const quadro = document.querySelector("[data-quadro-assinatura]");
  if (!quadro) return;
  const canvas = quadro.querySelector("canvas");
  const campo = document.querySelector('input[name="tracos"]');
  const contexto = canvas.getContext("2d");
  let tracos = [];
  let atual = null;
  let largura = 0;
  let altura = 0;

  try {
    const salvo = JSON.parse(campo.value || "null");
    if (salvo && Array.isArray(salvo.tracos)) {
      tracos = salvo.tracos;
      largura = salvo.w;
      altura = salvo.h;
    }
  } catch (erro) { tracos = []; }

  function ajustar() {
    const caixa = canvas.getBoundingClientRect();
    const escala = window.devicePixelRatio || 1;
    const larguraAnterior = largura;
    const alturaAnterior = altura;
    largura = caixa.width;
    altura = caixa.height;
    canvas.width = Math.round(largura * escala);
    canvas.height = Math.round(altura * escala);
    contexto.setTransform(escala, 0, 0, escala, 0, 0);
    if (larguraAnterior && alturaAnterior && (larguraAnterior !== largura || alturaAnterior !== altura)) {
      const fx = largura / larguraAnterior;
      const fy = altura / alturaAnterior;
      tracos = tracos.map((t) => t.map((p) => [p[0] * fx, p[1] * fy]));
    }
    desenhar();
  }

  function desenhar() {
    contexto.clearRect(0, 0, largura, altura);
    contexto.strokeStyle = "#172033";
    contexto.fillStyle = "#172033";
    contexto.lineWidth = 2.4;
    contexto.lineCap = "round";
    contexto.lineJoin = "round";
    tracos.forEach(function (traco) {
      if (traco.length === 1) {
        contexto.beginPath();
        contexto.arc(traco[0][0], traco[0][1], 1.4, 0, Math.PI * 2);
        contexto.fill();
        return;
      }
      contexto.beginPath();
      contexto.moveTo(traco[0][0], traco[0][1]);
      for (let i = 1; i < traco.length; i++) contexto.lineTo(traco[i][0], traco[i][1]);
      contexto.stroke();
    });
    quadro.classList.toggle("quadro-assinatura--preenchido", tracos.length > 0);
    campo.value = tracos.length ? JSON.stringify({ w: Math.round(largura * 10) / 10, h: Math.round(altura * 10) / 10,
      tracos: tracos.map((t) => t.map((p) => [Math.round(p[0] * 10) / 10, Math.round(p[1] * 10) / 10])) }) : "";
  }

  function ponto(e) {
    const caixa = canvas.getBoundingClientRect();
    return [e.clientX - caixa.left, e.clientY - caixa.top];
  }

  canvas.addEventListener("pointerdown", function (e) {
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    atual = [ponto(e)];
    tracos.push(atual);
    desenhar();
  });
  canvas.addEventListener("pointermove", function (e) {
    if (!atual) return;
    const p = ponto(e);
    const ultimo = atual[atual.length - 1];
    if (Math.abs(p[0] - ultimo[0]) + Math.abs(p[1] - ultimo[1]) < 1.5) return;
    atual.push(p);
    desenhar();
  });
  ["pointerup", "pointercancel", "pointerleave"].forEach((evento) => canvas.addEventListener(evento, () => { atual = null; }));

  document.querySelector("[data-limpar-assinatura]").addEventListener("click", function () {
    tracos = [];
    desenhar();
  });
  document.querySelector("[data-desfazer-assinatura]").addEventListener("click", function () {
    tracos.pop();
    desenhar();
  });

  /* assinatura digitada: espelha o nome completo */
  const nome = document.querySelector('input[name="nome"]');
  const espelho = document.querySelector("[data-espelho-nome]");
  if (nome && espelho) {
    const copiar = () => { espelho.textContent = nome.value.trim() || "Seu nome aparecerá aqui"; };
    nome.addEventListener("input", copiar);
    copiar();
  }

  window.addEventListener("resize", ajustar);
  document.addEventListener("change", function (e) {
    if (e.target.name === "tipo_assinatura") requestAnimationFrame(ajustar);
  });
  ajustar();
})();
