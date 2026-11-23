/* Relógio dos avisos. Roda num "worker" porque o navegador segura os temporizadores das abas em segundo plano
   (até 1 vez por minuto); o worker continua no ritmo pedido e a aba consulta o servidor na hora. */
let temporizador = null;
onmessage = function (e) {
  clearInterval(temporizador);
  temporizador = setInterval(function () { postMessage(0); }, Math.max(2000, e.data | 0));
};
