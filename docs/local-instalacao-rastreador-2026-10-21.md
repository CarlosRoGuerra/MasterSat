# Local de instalação do rastreador

Em **Rastreadores → Novo rastreador** ou **Editar**, o campo opcional **Local de instalação** fica ao lado de **Data de instalação**. O usuário pode digitar até 120 caracteres, por exemplo: “Atrás do painel, lado do motorista”.

Ao salvar, o texto é armazenado no campo existente `trackers.install_location` e aparece em **Detalhes → Dados → Local de instalação**. A edição reapresenta o texto salvo. Apagar o conteúdo e salvar remove a informação; quando vazio, os detalhes mostram “Não informado”. O campo também acompanha a edição técnica feita junto com a troca de cliente.

A API e o banco já suportavam esse dado. Não é necessário executar SQL ou migration.

## Atualização na VPS

```bash
cd ~/MasterSat
git switch main
git pull --ff-only origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml build frontend
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps frontend
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T nginx nginx -s reload
```

Atualize a página com Ctrl+F5 e abra a edição de um rastreador.

## Validação

Os cinco testes existentes da tela de rastreadores passaram. TypeScript passou; ESLint sem erros e com três avisos preexistentes da página. A verificação foi feita em uma cópia isolada do código, preservando as outras alterações locais.
