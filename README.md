# itau-xj7-reusable-workflows

Executores compartilhados para produtos de containers. O primeiro consumidor
é `alric-corp/itau-xj7-containers-image-base`.

| API | Responsabilidade | Permissões do chamador |
| --- | --- | --- |
| `.github/workflows/validate-apko-images.yml` | Melange, build Apko único, scan amd64/arm64 e artifact OCI aprovado | `contents: read` |
| `.github/workflows/test-runtime-images.yml` | Executar contratos do consumidor sobre os artifacts candidatos das duas arquiteturas | `contents: read`, `actions: read` |
| `actions/setup-trivy` | Instalar e verificar a versão governada do Trivy | Nenhuma permissão adicional |

Estes workflows seguem o [contrato Apko](docs/apko-contract.md). Um novo produto
precisa implementar esse contrato; não são um pipeline genérico de Dockerfiles.
O checkout dos executores lê o repositório chamador, onde ficam os manifests,
scripts de domínio e testes. Nenhum workflow recebe comandos para executar como input.

Gatilhos, catálogo, AWS, publicação/assinatura, promoção, recuperação, saúde e
triagem permanecem no consumidor. A action composta executa com as permissões
do job chamador; ela não cria isolamento.

Consuma sempre `owner/repo/.github/workflows/arquivo.yml@<SHA completo>` no nível
de job, ou `owner/repo/actions/setup-trivy@<SHA completo>` no nível de step.
Os chamadores concretos estão no `image-base`. Não use `@main`, `@v1` ou
`secrets: inherit`. Os workflows compartilhados não possuem cron nem dispatch.

O Trivy da validação referencia a action deste repositório por um SHA anterior
que contém sua implementação. Isso evita uma referência circular ao próprio
commit. Quando a action mudar, publique o commit da action antes de atualizar
os consumidores, incluindo `validate-apko-images.yml`; promoção e recuperação
devem adotar o mesmo SHA da action. Versões não são sobrescritas pelo consumidor.

Mudanças nos contratos de paths, inputs, artifacts ou permissões exigem revisão
dos consumidores. Mudanças incompatíveis devem ganhar uma nova API (por exemplo,
outro nome de workflow), preservando a anterior durante a migração.

`ci.yml` executa testes de contrato, hardening, actionlint e instalação real do
Trivy em PRs, inclusive de documentação. Dependabot propõe atualizações de
Actions e workflows; Renovate está configurado para os digests e a versão do
Trivy, sem automerge. A instalação/ativação do Renovate ainda precisa ser conferida.

Validação local:

```sh
python3 -m pip install PyYAML==6.0.3
python3 -B -m unittest discover -s tests -v
python3 -B scripts/check_contracts.py
actionlint .github/workflows/*.yml
```

Antes da adoção em produção, verificar required checks (`test`, `lint-workflows`,
`smoke-trivy`), aprovação de code owner, descarte de aprovações antigas e
`enforce_admins`. `CODEOWNERS` requer donos com escrita e proteção efetiva.
Na consulta de 09/09/2026 o repositório era público e a `main` ainda não estava
protegida; os arquivos deste PR não ativam essas configurações remotas.

Referências: [reuso, SHA e permissões](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows),
[contexto do chamador e checkout](https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations),
[identidade OIDC de workflows reutilizáveis](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-with-reusable-workflows).
