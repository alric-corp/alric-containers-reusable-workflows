# Governança do executor compartilhado

`main` exige os checks `test`, `lint-workflows` e `smoke-trivy` emitidos pelo
GitHub Actions, branch atualizada, uma aprovação independente, revisão de
CODEOWNERS, descarte de aprovações após alterações e resolução de conversas.
Administradores também seguem essas regras; force push e exclusão são proibidos.
A configuração reproduzível está em `policies/main-protection.json`.

O time `@alric-corp/github_xj7_maintainer` recebe escrita no sandbox e `@vigcf`
é dono adicional. CODEOWNERS começa a valer quando o arquivo entra na branch
base; até lá a aprovação independente já é obrigatória na proteção remota.

Aplicação por um administrador:

```sh
gh api --method PUT orgs/alric-corp/teams/github_xj7_maintainer/repos/alric-corp/alric-containers-reusable-workflows -f permission=push
gh api --method PUT repos/alric-corp/alric-containers-reusable-workflows/branches/main/protection --input policies/main-protection.json
gh api --method PUT repos/alric-corp/alric-containers-reusable-workflows/actions/permissions -F enabled=true -f allowed_actions=all -F sha_pinning_required=true
```

Os consumidores continuam fixados por SHA. Um check verde na branch do
executor não equivale à aprovação humana exigida para integrar a `main`.
O PR #2 corrige a referência Trivy após o rename e deve seguir o mesmo gate.

`renovate.json` cobre Apko, Melange, actionlint e Trivy; Dependabot cobre
Actions. Em 10/09/2026, a API da organização informou zero instalações de
GitHub Apps. É necessário instalar o Renovate selecionando apenas os dois
repositórios de containers e aceitar o onboarding; a configuração sozinha
não liga o serviço. Automerge permanece desligado.

Na organização corporativa, substituir os donos e reaplicar/verificar as
proteções e permissões com as identidades reais. O resultado do sandbox não
constitui aceite de produção.
