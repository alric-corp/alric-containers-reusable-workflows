# EVIDENCE — 2026-09-11-ai-workflow

Estado: CONFIGURAÇÃO LOCAL VERIFICADA.

## Identidade e escopo

- Data: 11/09/2026.
- Ambiente: macOS arm64, Python 3.9.6, PyYAML 6.0.3; actionlint 1.7.12 disponível.
- Repositório: alric-containers-reusable-workflows; branch main.
- Commit base: `94e6ebdba74bd80f5f5f8ce713c5529df34263d2`.
- Mudança validada: working tree local desta configuração, incluindo arquivos novos.
  Ainda sem commit/PR/run remoto; estes resultados não são prova de um SHA futuro.
- Implementador e auto-revisor: Codex, nesta sessão.

## Comandos executados na raiz

| Comando | Exit code | Resultado |
| --- | --- | --- |
| python3 -B tools/check_ai_context.py | 0 | Arquivos, import e links locais válidos |
| python3 -B -m unittest discover -s tests -v | 0 | 24 testes aprovados |
| python3 -B scripts/check_contracts.py | 0 | Contratos e hardening aprovados |
| actionlint .github/workflows/*.yml | 0 | Sem diagnósticos |
| git diff --check | 0 | Sem erros de whitespace nos arquivos rastreados |

Os dez testes AIContextTests foram descobertos pela suíte existente. Incluem
cenários de arquivo obrigatório ausente/vazio, import incorreto do Claude,
referência canônica ausente no Copilot, link quebrado, escape para diretório
irmão, symlink externo e spec incompleta. Exemplos em código e URLs externas
não são tratados como arquivos locais. O validador não acessa a rede.

## Aceite

| Critério | Resultado | Evidência |
| --- | --- | --- |
| A01 | PASS | Validador real e revisão dos três pontos de entrada |
| A02 | PASS | Workflow proporcional, seis templates e quatro prompts portáveis |
| A03 | PASS | Mapa comparado com README, comandos, testes e workflows existentes |
| A04 | PASS | Descoberta normal executou AIContextTests; CI existente usa essa suíte sem filtro de paths |
| A05 | PASS | Diff restrito a contexto/docs, tooling, testes e ignores; nenhum pin, workflow ou contrato de runtime alterado |
| N01 | PASS | Cenários negativos executados nos testes do validador |

## Revisão e limitações

Auto-revisão do diff e arquivos novos, com comparação às fontes locais.
Não houve revisão independente; esta entrega não altera controles de release
ou contratos do produto. A atribuição de autoria existente foi preservada onde
já estava definida; nenhuma configuração Git global foi alterada.

Instalação real de Trivy no smoke-trivy e execução autenticada dos consumidores não foram realizadas: os executores não foram alterados.

Não foram abertas novas sessões de Codex, Claude Code ou Copilot para observar
carregamento/obediência das instruções. O [guia](../../docs/ai/README.md) contém
o procedimento para essa confirmação. Teste estrutural não mede comportamento
do modelo. Não houve CI remoto, commit, push, publicação ou mudanças em contas.

O requisito local está atendido. A confirmação nas interfaces permanece
NOT RUN e deve ser registrada como observação separada ao iniciar novas sessões.
