"""Política de senha — a mesma em todo lugar que DEFINE uma senha.

Usada na criação/edição de usuário pelo admin, no reset, no cadastro, no
bootstrap do admin inicial e no script de recuperação. Antes só o reset e o
cadastro validavam; a administração de usuários aceitava senha vazia ou de
um caractere (SEC-04).

Não é aplicada no LOGIN: contas existentes com senha antiga fraca continuam
entrando (o hash não é alterado); a política vale na próxima troca.

Regras:
- 8 caracteres ou mais, com minúscula, maiúscula, número e caractere especial
  (a regra que o reset já exigia — espaço conta como especial);
- no máximo 72 BYTES em UTF-8: o bcrypt ignora o que passa disso, então uma
  senha maior seria silenciosamente truncada (acentos ocupam 2 bytes);
- sem caracteres de controle (NUL quebra o bcrypt; quebra de linha/tab em
  senha é quase sempre colagem acidental).
"""
from __future__ import annotations

import unicodedata

MIN_LENGTH = 8
MAX_BYTES = 72


def password_problems(value: str | None) -> list[str]:
    """Lista de violações (vazia = senha aceita). Nunca inclui a senha."""
    if value is None or value == '':
        return ['Informe a senha']
    problems: list[str] = []
    if len(value) < MIN_LENGTH:
        problems.append(f'A senha deve ter ao menos {MIN_LENGTH} caracteres')
    if len(value.encode('utf-8')) > MAX_BYTES:
        problems.append(f'A senha deve ter no máximo {MAX_BYTES} bytes (acentos contam em dobro)')
    if any(unicodedata.category(c) == 'Cc' for c in value):
        problems.append('A senha não pode conter caracteres de controle')
    if value.strip() == '':
        problems.append('A senha não pode ser só espaços')
    composicao = [
        any(c.islower() for c in value),
        any(c.isupper() for c in value),
        any(c.isdigit() for c in value),
        any(not c.isalnum() for c in value),
    ]
    if not all(composicao):
        problems.append('A senha deve conter letra maiúscula, minúscula, número e caractere especial')
    return problems


def validate_password(value: str | None) -> str:
    """Validador pydantic/uso direto: devolve a senha ou levanta ValueError."""
    problems = password_problems(value)
    if problems:
        raise ValueError('; '.join(problems))
    return value  # type: ignore[return-value]
