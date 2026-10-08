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
| `melange-reproduction-reference` | Captura HGC-03 do build de referência (data exata, materiais e índices das dependências) | 30 dias |
| `melange-reproducibility` | Evidência HGC-03 aprovada; só existe com status `REPRODUCED` | 30 dias |
| `melange-reproducibility-diagnostics-<attempt>` | Diagnóstico de rebuild reprovado; nunca alimenta a validação | 30 dias |
| `build-scans-<framework>-<attempt>` | Relatórios em `reports/` | 30 dias |
| `sbom-<framework>-<attempt>` | SPDX original, lock, data/revisão e índice validado | 30 dias |
| `validated-oci-<framework>` | Layout OCI + índice validado; só existe após sucesso | 3 dias |
| `runtime-<framework>-<attempt>` | Relatórios `reports/runtime-*.json` | 30 dias |

Um mesmo run deve chamar a validação uma única vez com o lote completo:
`melange-repo`, `melange-reproduction-reference`, `melange-reproducibility` e
`validated-oci-*` são nomes do protocolo compartilhados dentro do run. Não chame esta API duas vezes em paralelo no mesmo run.
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

**Correção (HGC-03).** A análise do HGC-02 (PR #13) registrou que
`--apk-cache-dir` só continha streams expandidos em diretórios temporários de
nome aleatório. Estava errado: a listagem usada (`find -type f`) omitiu os
nomes endereçados por conteúdo, que são symlinks (`<sha1>.ctl.tar.gz`,
`<sha256>.dat.tar.gz`, `<sha256>.dat.tar`) para os streams de controle e de
dados guardados em `expand-apk*/`. Continua correto que o HGC-02 não capturava
checksums materiais das dependências e que os outputs usados por ele (lock,
`.PKGINFO`, `APKINDEX`, SBOM, SLSA) não os expõem. Novos builds podem capturar
essa identidade pelo cache (HGC-03); os runs históricos, inclusive
37614260125, 37729369156 e 37731450779, continuam com
`DEPENDENCY_MATERIAL_IDENTITY = NOT_PROVEN`. O `environment_digest` não muda:
continua sem a data do build, a chave de assinatura e os materiais; a
identidade de reprodução do HGC-03 é adicional.

### HGC-03 — reprodutibilidade do pacote CA

**Igualdade.** Para o pacote de `melange/<melange-config>` em `x86_64` e
`aarch64`, um rebuild em outro job e outro runner do mesmo run, com os mesmos
arquivos de origem, configuração, Melange e binfmt pinados, string exata de
`BUILD_DATE` e materiais de dependência idênticos, precisa produzir os mesmos
bytes originais dos streams de controle e de dados de cada APK. Nada é
normalizado antes da comparação (tar, gzip, timestamps, SBOM, `.PKGINFO`).
Ficam fora da igualdade o stream de assinatura, o `APKINDEX` e o APK assinado
completo, porque cada build usa sua própria chave efêmera; ainda assim cada
build verifica a assinatura RSA-SHA256 dos seus APKs e do seu `APKINDEX` com a
sua chave pública, e o `APKINDEX` precisa descrever exatamente os APKs do
próprio build (`C:` e `S:`). Não é uma afirmação sobre o Melange em geral nem
sobre o catálogo; payloads com mais de um bloco pgzip não foram exercitados.

**Data.** `BUILD_DATE` continua vindo de `git show -s --format=%cI HEAD`. A
string RFC3339 completa, com offset, é input: o mesmo instante escrito em UTC
produz outros bytes (`created` do SBOM e mtimes). A captura registra a string,
a origem e o commit, o `SOURCE_DATE_EPOCH` do host (`%ct`, exigido igual ao
mesmo instante), a presença dessa variável no processo Melange e o parâmetro
aplicado. O Melange v0.61.2 deixa `SOURCE_DATE_EPOCH` do próprio processo
sobrescrever `--build-date` (`pkg/build/build.go`); o `docker run` não passa
`-e`/`--env` e cada job confere por `docker image inspect` que a `Config.Env`
da imagem pinada não a define, logo o registro é `absent` e o parâmetro é
`--build-date`, confirmado nos outputs (`builddate` igual ao epoch e `created`
igual à string exata). O rebuild recebe a string registrada e só a aceita se
for o `%cI` do mesmo commit. Nenhuma data é normalizada nem tirada do relógio.

**Materiais das dependências.** O step de build, com o mesmo texto nos dois
jobs, monta `--apk-cache-dir` em `$RUNNER_TEMP/melange-apk-cache`: vazio no
início de cada job, fora de `melange/`, dos arquivos de origem e do
`environment_digest`. O adapter lê apenas o layout observado do go-apk do apko
v1.4.6 embutido no Melange pinado e falha com qualquer outra estrutura:
`<repositório url-escaped>/<arch>/APKINDEX/<n>.tmp` com um symlink
`<etag>.tar.gz`, e `<repositório>/<arch>/<nome>-<versão>/expand-apk<n>/` com
`stream-0.tar.gz` (controle), `stream-1.tar.gz` (dados) e `stream-1.tar`
(dados expandidos), anunciados por três symlinks endereçados por conteúdo.
Esses symlinks são tratados à parte da origem (a rejeição de symlinks em
`melange/` do HGC-02 continua): precisam ser relativos, resolver dentro do
diretório do próprio pacote ou índice e apontar para o stream esperado; o nome
nunca é prova, os SHA-1/SHA-256 são recalculados dos bytes. Como o Melange roda
como root, `sudo chown -hR` entrega o cache ao usuário do runner sem seguir
symlinks. Para cada dependência consumida são registrados nome, versão,
arquitetura, repositório, SHA-256 e tamanho do stream de controle, `C:`/Q1,
`datahash`, SHA-256 e tamanho do stream de dados, o índice usado e as
verificações. O inventário precisa ser igual ao lock nome/versão de cada
target, sem faltas, extras ou streams de outro pacote (`.PKGINFO` confere
nome, versão e arch). O cache guarda só controle e dados: o APK original
completo da dependência não é recuperado.

**Índice.** O `APKINDEX` lido é o `RESOLUTION_INDEX`: o arquivo que o próprio
go-apk baixou, gravou no cache e serve ao resolvedor do build, não um índice
consultado depois. A assinatura é verificada com o arquivo do keyring
declarado (`.SIGN.RSA256.<arquivo>`, SHA-256 do receipt HGC-02) e a entrada
nome/versão/arch precisa existir uma única vez com `C:` igual ao Q1 do controle
consumido. Nenhum `VERIFICATION_INDEX` separado é consultado. A origem é
demonstrada pelo mecanismo da ferramenta pinada, não por atestado do
repositório; chaves que o apko descubra no repositório continuam o P1-03.

**Jobs e gate.** No `melange-bundle`, depois do receipt HGC-02, `Capture
Melange reproduction reference` grava em `RUNNER_TEMP` a captura
`melange-reproduction-reference.json` (kind `melange-reproduction-reference`:
producer com job e runner, SHA-256 dos receipts HGC-01/HGC-02, parâmetros
temporais, materiais e índices), publicada em artifact próprio sem alterar
`melange-repo`. O novo job `melange-reproduce` baixa `melange-repo` e a
captura para `RUNNER_TEMP`, fora do workspace; `Prepare reproduction inputs`
exige JSON canônico, kinds, binding por hash, mesmo repositório, ref, SHA e
run, attempt da referência menor ou igual ao atual, outro job, a mesma árvore
`melange/`, a mesma configuração e imagem, e exporta a string registrada.
O job gera nova chave efêmera, roda o mesmo step de build e `Record Melange
reproducibility evidence` reconfere a ferramenta (digest, plataforma, versão),
o binfmt (digest), as assinaturas dos dois builds, o lock do rebuild e os
materiais, e compara. Status:

- `REPRODUCED`: materiais iguais e controle/dados iguais;
- `INPUTS_DIFFER`: os materiais diferem (por exemplo, o Wolfi publicou outra
  versão entre os jobs); não é falha de determinismo do mesmo build. Um
  re-run só dos jobs com falha reaproveita a referência do attempt anterior;
  para capturar uma referência nova é preciso re-executar todos os jobs;
- `OUTPUTS_DIFFER_WITH_EQUAL_RECORDED_INPUTS`: materiais iguais e streams
  diferentes;
- `NOT_INDEPENDENT`: mesmo runner ou job da referência.

Só `REPRODUCED` gera `melange-reproducibility-evidence.json`; os outros
status gravam diagnóstico (`melange-reproducibility-diagnostics-<attempt>`) e
falham o job. Isso muda o comportamento operacional: `validate` passa a
depender de `melange-bundle` e `melange-reproduce`, e `Require reproduced
Melange package` roda antes do build OCI. O gate exige evidência canônica
`REPRODUCED` e `CROSS_JOB_SAME_RUN`, receipts iguais aos bytes de
`melange-repo`, mesmo repositório, SHA, ref e run, attempts até o atual,
runner e job distintos, digest recalculado, APKs candidatos iguais aos da
referência, assinaturas verificadas e todos os resultados iguais. Falha ou
ausência bloqueia os candidatos; diagnóstico nunca vira aprovação. Publicador,
política Trivy e promoção não mudam.

**Evidência.** `melange-reproducibility-evidence.json`: schema fechado v1,
kind `melange-reproducibility-evidence`, JSON canônico. Seções: `reference`
(A, build de referência), `rebuild` (B), `reproduction_inputs` e
`reproduction_input_digest` (C), `results` (D), `signatures` (E), `limits`
(F), além de `scope` e `receipts` (SHA-256 dos bytes dos receipts HGC-01,
HGC-02 e da captura, e o `environment_digest`). `reproduction_input_digest` é
o SHA-256 canônico de `reproduction_inputs`: ferramenta, host, binfmt,
configuração, arquivos de origem, repositórios, keyring, data (string exata,
parâmetro e ausência de `SOURCE_DATE_EPOCH` no processo) e identidade material
das dependências por target. Ficam fora chaves, producers, índices (mudam com
o tempo), verificações e resultados. O arquivo é copiado sem reserialização
para `<framework>.oci/` e para o artifact de replay; nenhuma chave privada sai
do job que a gerou.

**Limites.** A independência é `CROSS_JOB_SAME_RUN` (outro job e runner, mesmo
`run_id`), não `CROSS_RUN`, e não equivale a revisão independente. Hashes
registrados não garantem replay futuro, mirror ou disponibilidade dos pacotes
Wolfi. A evidência vive em artifacts de workflow; a custódia durável é do
HGC-04. Probes locais com a ferramenta real não são prova hospedada. Status:
`HGC03_STATUS = IMPLEMENTED`, `HGC03_HOSTED_PROOF = NOT_PROVEN`.

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
