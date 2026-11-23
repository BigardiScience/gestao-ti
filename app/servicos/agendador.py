"""Tarefas automáticas (lembretes, resumo diário, backup e limpeza).

Roda numa thread dentro da própria aplicação. Se houver mais de um processo (ex.: vários
workers), uma trava no banco garante que cada tarefa execute uma vez só.
Também é possível rodar manualmente: `flask --app wsgi executar-tarefas`.
"""
import threading
import time
from datetime import datetime, timedelta

from ..db import consultar, executar
from ..util import agora, agora_bd, fuso, para_bd
from .configuracoes import cfg

_iniciado = False
_trava_inicio = threading.Lock()


def _tarefas():
    from . import avisos_chamados, backup, notificacoes
    return {
        "chamados": {"funcao": avisos_chamados.job_chamados, "intervalo_min": 1,
                     "rotulo": "Chamados: lembretes, prazos e encerramento automático"},
        "chamados_resumo": {"funcao": avisos_chamados.job_resumo, "hora": lambda: cfg("chamados_resumo_hora"),
                            "rotulo": "Resumo diário dos chamados"},
        "lembretes": {"funcao": notificacoes.job_lembretes, "intervalo_min": 60, "rotulo": "Lembretes de assinatura"},
        "resumo_diario": {"funcao": notificacoes.job_resumo_diario, "hora": lambda: cfg("resumo_diario_hora"),
                          "rotulo": "Resumo diário para o TI"},
        "backup": {"funcao": backup.job_backup, "hora": lambda: cfg("backup_hora"), "rotulo": "Backup automático"},
        "limpeza": {"funcao": notificacoes.job_limpeza, "hora": lambda: "03:40", "rotulo": "Limpeza de temporários"},
    }


def _proxima(tarefa: dict, depois_de: datetime) -> str:
    if "intervalo_min" in tarefa:
        return para_bd(depois_de + timedelta(minutes=tarefa["intervalo_min"]))
    try:
        hora, minuto = (int(x) for x in (tarefa["hora"]() or "02:30").split(":")[:2])
    except ValueError:
        hora, minuto = 2, 30
    local = depois_de.astimezone(fuso())
    alvo = local.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if alvo <= local:
        alvo += timedelta(days=1)
    return para_bd(alvo)


def reprogramar(nomes=None):
    tarefas = _tarefas()
    for nome in nomes or tarefas:
        executar("UPDATE tarefas SET proxima_execucao = ? WHERE nome = ?", (_proxima(tarefas[nome], agora()), nome))


def situacao() -> list[dict]:
    tarefas = _tarefas()
    linhas = {l["nome"]: l for l in consultar("SELECT * FROM tarefas")}
    return [{"nome": nome, "rotulo": t["rotulo"], **linhas.get(nome, {})} for nome, t in tarefas.items()]


def executar_pendentes(app, forcar: list | None = None) -> list[tuple[str, str]]:
    from .configuracoes import url_base
    with app.app_context():
        base = url_base()
    resultados = []
    with app.test_request_context("/", base_url=base):
        tarefas = _tarefas()
        for nome, tarefa in tarefas.items():
            if forcar and nome not in forcar:
                continue
            executar("INSERT OR IGNORE INTO tarefas (nome, proxima_execucao) VALUES (?, ?)",
                     (nome, _proxima(tarefa, agora()) if "hora" in tarefa else agora_bd()))
            agora_txt = agora_bd()
            trava = para_bd(agora() + timedelta(minutes=30))
            sql = "UPDATE tarefas SET bloqueada_ate = ? WHERE nome = ? AND (bloqueada_ate IS NULL OR bloqueada_ate < ?)"
            parametros = [trava, nome, agora_txt]
            if not forcar:  # execução normal: só o que já venceu
                sql += " AND (proxima_execucao IS NULL OR proxima_execucao <= ?)"
                parametros.append(agora_txt)
            if executar(sql, parametros).rowcount != 1:
                continue  # ainda não é hora, ou outro processo já está executando
            try:
                resultado = tarefa["funcao"]() or "Concluída."
            except Exception as erro:  # nunca deixa a thread morrer
                app.logger.exception("Tarefa %s falhou", nome)
                resultado = f"Falhou: {erro}"
            executar("UPDATE tarefas SET ultima_execucao = ?, ultimo_resultado = ?, proxima_execucao = ?, "
                     "bloqueada_ate = NULL WHERE nome = ?",
                     (agora_bd(), str(resultado)[:500], _proxima(tarefa, agora()), nome))
            resultados.append((nome, resultado))
    return resultados


def _laco(app):
    time.sleep(20)
    while True:
        try:
            executar_pendentes(app)
        except Exception:
            app.logger.exception("Erro no agendador de tarefas")
        time.sleep(60)


def iniciar(app):
    global _iniciado
    with _trava_inicio:
        if _iniciado:
            return
        _iniciado = True
    threading.Thread(target=_laco, args=(app,), name="agendador", daemon=True).start()
    app.logger.info("Agendador de tarefas iniciado.")
