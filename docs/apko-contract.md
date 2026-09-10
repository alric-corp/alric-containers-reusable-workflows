# Contrato Apko/OCI — versão 1

Este contrato preserva o protocolo já usado pelo `image-base`. O consumidor
mantém os scripts porque eles definem o catálogo, os gates e os contratos
funcionais do produto; a biblioteca mantém a ordem dos passos, as ferramentas,
as permissões, os limites e a transferência dos artifacts. O SHA da biblioteca
e o commit do consumidor juntos definem a execução.

## Validação

Input obrigatório `frameworks`: string contendo array JSON não vazio de nomes
únicos do catálogo. `.github/scripts/validate_inputs.py` deve rejeitar nomes
inválidos e traversal antes do build. O job do bundle falha antes da matriz
se a entrada não for válida; cada executor da matriz também valida a entrada.

Arquivos no consumidor:

- `frameworks/<nome>.yaml` e suas inclusões em `distroless/`;
- `melange/bundle-pem-test.yaml` e os certificados/configurações referenciados;
- `.github/scripts/validate_inputs.py`, `oci_artifact.py`, `scan_images.py`,
  `report_unfixed_cves.py`, `tool_versions.py` e seus módulos importados.

Os scripts usam as mesmas interfaces CLI versionadas no `image-base`:
`oci_artifact.py prepare <layout>`, `scan_images.py oci <layout>`,
`report_unfixed_cves.py oci <layout>` e
`tool_versions.py validation reports/tool-versions.json`.

O scan bloqueante precisa aprovar amd64 e arm64. O relatório de CVEs sem
correção é informativo; ele não substitui nem neutraliza o gate.

| Artifact | Conteúdo/consumo | Retenção |
| --- | --- | --- |
| `melange-repo` | Pacotes, chave pública efêmera e versão Melange | 1 dia |
| `build-scans-<framework>-<attempt>` | Relatórios em `reports/` | 30 dias |
| `validated-oci-<framework>` | Layout OCI + índice validado; só existe após sucesso | 3 dias |
| `runtime-<framework>-<attempt>` | Relatórios `reports/runtime-*.json` | 30 dias |

Um mesmo run deve chamar a validação uma única vez com o lote completo:
`melange-repo` e `validated-oci-*` são nomes do protocolo compartilhados dentro
do run. Não chame esta API duas vezes em paralelo no mesmo run.
Retenção faz parte da API; uma alteração exige atualizar a política do consumidor.

## Contratos de runtime

Inputs: `framework` (string obrigatória) e `artifact-run-id` (string opcional,
vazia significa o run atual). O run deve pertencer ao próprio consumidor;
não existe input para buscar artifacts em outro repositório.

O consumidor fornece `.github/scripts/runtime_images.py`, seus módulos e
`tests/runtime/`, incluindo probes e projetos multi-stage. O módulo expõe
`supported(framework)` e, para contratos compilados, `project(framework)`.
A API original Node/Python também é aceita: `runtime(framework)` valida o nome
e a CLI recebe apenas layout e framework, sem `--dev-layout`.
Framework desconhecido e run ID
inválido falham antes de qualquer execução do candidato. A CLI aceita
`runtime_images.py <layout> <framework> --dev-layout <layout-dev>`.

Quando houver contrato compilado, a variante `-dev` vem do mesmo run candidato.
O executor não reconstrói a imagem base; ele pode compilar o projeto de teste
sobre a variante de build candidata. O consumidor decide a cobertura exigida
e deve bloquear publicação se a evidência obrigatória estiver ausente ou falhar.

## Limites de confiança

Validação recebe leitura; runtime recebe também `actions: read` para baixar
artifacts. Nenhum desses executores solicita OIDC, AWS ou secrets. Use
`pull_request` para código de PR. Os scripts e os testes do consumidor precisam
passar pela revisão dos donos do produto.

Publicação e assinatura continuam no job `build-push` de
`image-base/.github/workflows/build-base-images.yml`. Promoção e recuperação
continuam usando a identidade exata desse assinador. Uma futura extração do
publicador exige prova de compatibilidade com imagens históricas, OIDC e
provenance; não se resolve isso ampliando a identidade para um wildcard.
