"""Regras de negócio da plataforma (independentes das telas)."""


class ErroNegocio(Exception):
    """Uma regra de negócio impediu a operação. A mensagem é mostrada ao usuário."""


class ErroValidacao(ErroNegocio):
    """Campos inválidos num formulário. `erros` mapeia campo -> mensagem."""

    def __init__(self, erros: dict, mensagem: str = "Revise os campos destacados."):
        self.erros = erros
        super().__init__(mensagem)
