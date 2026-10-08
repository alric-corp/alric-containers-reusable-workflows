# Contrato Apko/OCI — versões 1 e 2

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
- `melange/<melange-config>` (default legado: `bundle-pem-test.yaml`);
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
| `melange-repo` | Pacotes, chave pública efêmera, versão Melange, receipt binfmt e evidência do ambiente Melange | 30 dias |
| `build-scans-<framework>-<attempt>` | Relatórios em `reports/` | 30 dias |
| `sbom-<framework>-<attempt>` | SPDX original, lock, data/revisão e índice validado | 30 dias |
| `validated-oci-<framework>` | Layout OCI + índice validado; só existe após sucesso | 3 dias |
| `runtime-<framework>-<attempt>` | Relatórios `reports/runtime-*.json` | 30 dias |

Um mesmo run deve chamar a validação uma única vez com o lote completo:
`melange-repo` e `validated-oci-*` são nomes do protocolo compartilhados dentro
do run. Não chame esta API duas vezes em paralelo no mesmo run.
Retenção faz parte da API; uma alteração exige atualizar a política do consumidor.

### HGC-01 — identity binfmt

O input `image` da setup-qemu-action usa a constante `BINFMT_IMAGE`, uma
referência `docker.io/tonistiigi/binfmt:qemu-v10.2.3-68@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0`.
O digest do índice multiarch e a tag foram conferidos diretamente no registry
em 2026-10-05. APKO, Melange e a política Trivy permanecem os mesmos.

Após a instalação, o producer verifica `RepoDigests` da imagem inspecionada,
exige igualdade com o digest pedido e consulta diretamente o container:
`--version` fornece a versão QEMU e a saída JSON de status confirma
`linux/arm64` e `qemu-aarch64`. Não usa o log do workflow como authority.
Tag sem digest, `latest`, digest divergente, versão ausente e emulação
indisponível falham antes do build do pacote.

`melange/binfmt-evidence.json` contém bytes JSON canônicos, schema fechado
v1 e kind `binfmt-evidence`: producer repository/ref/source SHA/run/attempt/
release ID/workflow, requested ref/digest, resolved digest, QEMU version e
architecture contract. O producer mantém a identidade real de execuções
de PR; elas não se tornam evidência nativa de release por normalização.
O arquivo original viaja no artifact `melange-repo` e é copiado para cada
layout como `binfmt-evidence.json`, incluindo os artifacts de replay.

Este receipt ainda depende da retenção de artifacts. HGC-04 deverá ancorar
seus bytes originais em authority durável create-once. O receipt não se
autoautentica, não autoriza publication, não habilita proving e não fecha o
gap hospedado sem execução, read-back e revisão. O consumidor só deve
adotar esta alteração após merge revisado e repin em SHA literal.

### HGC-02 — ambiente Melange

Antes do build, `Capture Melange environment inputs` verifica `MELANGE_IMAGE`
(`cgr.dev/chainguard/melange@sha256:…`, sem `latest`): `RepoDigests` do
repositório `cgr.dev/chainguard/melange` deve ser exatamente o digest pedido;
`melange version --json` precisa trazer uma versão de release, commit e
plataforma `linux/amd64`, igual à da imagem. Também registra SHA-256 de cada
arquivo regular de `melange/` (o diretório que o Melange copia para o
workspace), separando os arquivos gerados pelo run (`melange.rsa*`,
`melange-version.txt`, `binfmt-evidence.json`). A captura fica em
`RUNNER_TEMP` e nunca é publicada.

Depois do build, `Record structured Melange environment evidence` exige o
mesmo diretório de origem e lê o ambiente do lock que o próprio Melange grava
em cada APK (`.melange.yaml`, `environment.contents` travado pelo apko),
usando `melange query` da imagem pinada. Esse lock é o inventário completo
`nome=versão` do ambiente de cada arquitetura; o Melange v0.61.2 não expõe
checksums desses pacotes (o SLSA de `--generate-provenance` traz
`resolvedDependencies` vazio), então a identidade material dos pacotes do
ambiente não é declarada. Repositórios e keyring do lock precisam igualar a
configuração; o keyring precisa ser arquivo local com SHA-256. Chaves que o
apko descubra no repositório (`apk-configuration`) não aparecem no lock e
continuam sendo o risco residual do P1-03.

Cada APK de `x86_64` e `aarch64` é conferido pelo `.PKGINFO` (nome, versão,
arch, origin), pelo `datahash` (SHA-256 do stream de dados), pelo `APKINDEX`
assinado pela chave efêmera (`C:` e `S:`) e pelo SBOM gerado pela mesma versão
do Melange. `melange/melange-environment-evidence.json` (schema fechado v1,
kind `melange-environment-evidence`, JSON canônico) separa `environment`
(ferramenta, host, configuração, arquivos de origem, repositórios, keyring e
lock por arquitetura) dos dados da execução (producer, outputs, chave
efêmera e arquivos gerados). `environment_digest` é o SHA-256 da forma
canônica de `environment`: dois builds com o mesmo valor usaram o mesmo
ambiente Melange, sem afirmar que aconteceram no mesmo run nem que produzem
os mesmos bytes (HGC-03).

O arquivo original viaja em `melange-repo` e é copiado sem reserialização
para cada layout e artifact de replay como `melange-environment-evidence.json`.
Status: `HGC02_STATUS = IMPLEMENTED`, `PROVING_STATUS = NONE`; a prova
hospedada depende de merge revisado e repin do consumidor, e a persistência
durável continua com o HGC-04.

## Composição v2 (opt-in)

`locked-build: true` exige os módulos do consumidor
`scripts/certificates/prepare_anchors.py verify` e
`scripts/pipeline/artifacts/build_image.py <framework> <layout>`.
O primeiro rejeita âncoras não aprovadas antes da operação privilegiada;
o segundo resolve um lock, builda usando esse lock e a data do commit,
e registra annotations, SBOMs e insumos de replay. `melange-config` aceita
somente um nome YAML dentro de `melange/`, validado antes do Docker.

A data do pacote Melange também é fixada pelo commit do consumidor.
O perfil v1 continua aceito quando `locked-build` é falso. As APIs existentes
não mudam; a retenção do repositório Melange sobe para 30 dias para permitir
replay com os mesmos APKs e a chave pública. A disponibilidade de APKs Wolfi
na origem ainda limita o replay: lockfile não é um mirror de pacotes.

## Contratos de runtime

Inputs: `framework` (string obrigatória) e `artifact-run-id` (string opcional,
vazia significa o run atual). O run deve pertencer ao próprio consumidor;
não existe input para buscar artifacts em outro repositório.

O consumidor fornece `scripts/pipeline/runtime/runtime_images.py`, seus módulos
e `tests/runtime/`, incluindo probes e projetos multi-stage. O módulo expõe
`supported(framework)` e, para contratos compilados, `project(framework)`.
A API original Node/Python também é aceita: `runtime(framework)` valida o nome
e a CLI recebe apenas layout e framework, sem `--dev-layout`.
Framework desconhecido e run ID
inválido falham antes de qualquer execução do candidato. A CLI aceita
`runtime_images.py <layout> <framework> --dev-layout <layout-dev>`.

Durante a migração, o executor também aceita o layout legado
`.github/scripts/runtime_images.py`; novos consumidores devem adotar o caminho
em `scripts/pipeline/runtime/`.

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
