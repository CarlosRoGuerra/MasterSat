# Sessões, bootstrap, recuperação e política de senha

Vale a partir da Fase 01 (branch `fase-01-autorizacao-identidade`, migration
`d9e4f1a7b2c5`).

## 1. Sessões

**Sessão** = uma família de refresh tokens (`refresh_tokens.family`), criada no
login. O access token (30 min) carrega `sid` = essa família.

| Evento | Efeito |
|---|---|
| Login | nova família; access com `sid` |
| `POST /auth/refresh` | consome o refresh (uso único) e emite o sucessor na mesma família |
| Mesmo refresh chega de novo até **15 s** depois da troca (`REFRESH_REUSE_GRACE_SECONDS`) | **409**: nada é emitido e nada é revogado. São duas abas renovando juntas |
| Mesmo refresh chega depois disso, ou a família já foi revogada | reuso: **a família inteira é revogada** (401) |
| `POST /auth/logout` | revoga a família do cookie e, se vier, a do Bearer. O access dessa sessão passa a receber **401 imediatamente** |
| Troca de senha (admin, reset, script de recuperação) | revoga **todas** as famílias do usuário e grava `tokens_valid_from` |
| Usuário excluído ou inativo | todo token recusado (como antes) |

**Semântica de logout escolhida:** por sessão. O logout derruba só o navegador
onde foi feito (todas as abas dele compartilham a sessão). Outros dispositivos
continuam logados. Para derrubar todos os dispositivos, troque a senha. Não
existe "sair de todos" automático.

**Consumo único:** a rotação e o consumo de tokens de reset usam
`UPDATE ... WHERE <ainda não usado>` e conferem as linhas afetadas. No
PostgreSQL, duas requisições simultâneas se serializam na linha e só uma vence.
Isso foi provado com barreira entre leitura e gravação em
`test_fase01_refresh_rotacao.py` e `test_fase01_reset.py`.

**Frontend (`lib/api.ts`):** ao receber 409 no refresh, espera um pouco. Se a
outra aba já gravou um access novo no `localStorage`, usa esse; senão renova de
novo com o cookie novo. O logout envia o Bearer junto.

### Transição dos tokens emitidos antes desta versão

- Access sem `sid`: recusado (401). O frontend renova pelo cookie, que continua
  válido, e recebe um access com `sid`. **Não exige novo login.**
- Refresh anterior: continua válido e rotaciona normalmente.
- Downloads que usam `fetch` direto com o token guardado no estado da página
  (PDF, recibo, carnê, exports) não renovam sozinhos. Isso já acontecia quando o
  access expirava. Na primeira meia hora após o deploy, um desses downloads pode
  falhar uma vez; a próxima chamada comum da tela renova a sessão.

## 2. Admin inicial (bootstrap)

O bootstrap roda no startup (`services/admin_bootstrap.py`), **apenas com o
banco sem nenhum usuário** (nem excluído):

- `INITIAL_ADMIN_PASSWORD` definida e dentro da política: cria
  `INITIAL_ADMIN_EMAIL` como admin. O log registra o e-mail, **nunca a senha**;
- vazia ou fora da política: nada é criado, e o log explica o motivo e como
  provisionar;
- se já existe qualquer usuário, o bootstrap não faz nada. **Excluir o admin
  inicial é definitivo**: ele não volta no restart.

Depois do primeiro acesso, troque a senha e apague `INITIAL_ADMIN_PASSWORD` do
ambiente.

## 3. Recuperação de acesso administrativo

Use `backend/scripts/reset_admin_senha.py`, dentro do container do backend e com
acesso ao banco:

```
python scripts/reset_admin_senha.py --email admin@empresa.com.br            # pede a senha 2x, sem eco
python scripts/reset_admin_senha.py --email admin@empresa.com.br --gerar    # gera e mostra UMA vez no terminal
python scripts/reset_admin_senha.py --email admin@empresa.com.br --reativar # conta excluída/inativa: decisão explícita
python scripts/reset_admin_senha.py --criar --email admin@empresa.com.br    # só com banco sem usuários
```

- só atua em usuário **admin**; a senha passa pela política;
- reativar conta excluída ou inativa exige `--reativar`;
- encerra todas as sessões do usuário;
- grava uma linha em `audit_logs` (`method=CLI`, operador em `user_name`), sem
  a senha.

A recuperação é separada da reativação automática, que deixou de existir.

## 4. Redefinição de senha por e-mail

1. `POST /auth/forgot-password` sempre responde a **mesma mensagem**, seja o
   e-mail existente, inexistente, de conta inativa, de perfil cliente ou acima
   do limite.
2. Para conta elegível, gera um token de 32 bytes e grava **só o SHA-256**
   (`password_reset_tokens.token_hash`). Um pedido novo anula o anterior.
3. Depois da resposta, envia o link `FRONTEND_URL/resetar-senha?token=…` pelo
   **SMTP de Configurações > E-mail**, com até `PASSWORD_RESET_EMAIL_ATTEMPTS`
   (3) tentativas. O resultado fica em `sent_at`, `delivery_attempts` e
   `delivery_error`.
4. `POST /auth/reset-password` consome o token uma vez só. Token expirado,
   usado, inexistente ou de conta desativada depois do pedido recebe 400. Conta
   excluída não é reativada pelo reset.

Limites:

- por IP: `RATE_LIMIT_FORGOT_PASSWORD` (5/min);
- por conta: `PASSWORD_RESET_MAX_PER_WINDOW` pedidos em
  `PASSWORD_RESET_WINDOW_MINUTES` (3 em 15 min). Acima disso nada é emitido e o
  link já enviado não é anulado.

**Operação:** sem SMTP configurado, o pedido é aceito mas o e-mail não sai. O
log registra `ERROR ... SMTP não está configurado` e a linha fica com
`delivery_error = smtp_nao_configurado`. Configure o SMTP antes de divulgar a
tela "Esqueci minha senha".

Nunca vão para log: token, link, senha. Os logs identificam só `user_id`.

## 5. Política de senha

`core/password_policy.py` é a mesma regra para criação e edição de usuário
pelo admin, reset, cadastro, bootstrap e script de recuperação:

- 8 caracteres ou mais, com minúscula, maiúscula, número e caractere especial
  (espaço conta como especial);
- no máximo **72 bytes** em UTF-8, porque o bcrypt ignora o excedente (acentos
  ocupam 2 bytes);
- sem caracteres de controle, e não pode ser só espaços.

O **login não aplica a política**. Contas com senha antiga fraca continuam
entrando, o hash não é alterado e a regra vale na próxima troca. Se a empresa
quiser forçar a troca, falta um campo de troca obrigatória no primeiro acesso,
que ainda não existe (pendência).

Na edição de usuário, `password` ausente ou `null` mantém a senha atual.
`""` agora recebe 422 (antes gravava senha vazia).

Respostas 422 não ecoam mais o valor de campos sensíveis (`password`,
`new_password`, `token`, `code`, `state`…): o `input` volta como `***`.

## 6. Autorização do cooperado Ailos (state OAuth)

- `POST /ailos/connect` grava um `state` com prazo de `AILOS_STATE_TTL_MINUTES`
  (15). O login na Ailos precisa ser concluído dentro desse prazo.
- `POST /ailos/callback` aceita o state uma vez só, com consumo atômico. Replay,
  state vencido e callbacks simultâneos com o mesmo state são recusados. O
  contrato com a Ailos não mudou: `code` continua sendo o token do cooperado.
- Um state gravado antes desta versão não tem prazo e é tratado como expirado.
  Se um deploy acontecer no meio de uma autorização, clique de novo em
  "Reconectar Ailos".
- A renovação do token do cooperado trava a linha da integração até terminar.
  Uma renovação que chega enquanto outra acontece, ou até 60 s depois, usa o
  token recém-renovado e não chama a Ailos de novo. A cadência do keepalive
  (10 min) não mudou.
- A leitura dos segredos Fernet existentes não mudou.

## 7. Rollback

A migration `d9e4f1a7b2c5` é aditiva. O código anterior **não sobe** com o
banco nessa revisão, porque o alembic dele não conhece a revisão. Ordem
ensaiada (`docs/validacao/fase-01/ensaio-rollback.txt`):

1. parar o backend novo;
2. **com a imagem nova** (que tem o arquivo da migration):
   `alembic downgrade b3f8a1c9d2e7`;
3. **cortar as sessões, obrigatório**:
   `UPDATE users SET tokens_valid_from = now() + interval '1 second';`
4. subir a imagem anterior. Todos fazem login de novo.

O passo 3 é necessário porque o código anterior não confere o `sid`. Sem o
corte, um access encerrado por logout volta a valer até expirar (até 30 min).
Isso foi comprovado no controle negativo `ensaio-rollback-sem-corte.txt`.

O que o downgrade faz com os dados:

- pedidos de reset pendentes são marcados como usados (não há texto puro a
  restaurar); quem precisar pede de novo;
- `rotated_at` e `state_expires_at` são descartados;
- nenhum usuário, sessão revogada ou dado financeiro é alterado.

**Roll-forward:** basta subir a imagem nova. A migration converte em hash
qualquer token em texto puro criado nesse meio-tempo.
