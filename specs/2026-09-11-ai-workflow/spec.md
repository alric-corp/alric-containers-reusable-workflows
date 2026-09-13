# SPEC — 2026-09-11-ai-workflow

## Objetivo
Aplicar o framework de engenharia assistida por IA ao uso pessoal com Codex,
Claude Code e GitHub Copilot, com contexto persistente e continuidade entre ferramentas.

## Requisitos
- R1: manter uma fonte de instruções do projeto em AGENTS.md e adaptadores pequenos.
- R2: oferecer fluxo simples para mudanças pequenas e spec/aceite/plano/tasks/evidência para mudanças não triviais.
- R3: documentar contexto e comandos reais deste repositório, sem depender do diretório pai.
- R4: permitir passagem de contexto e revisão em outra sessão/ferramenta.
- R5: verificar offline integridade das instruções e links no gate de testes existente.
- R6: preservar controles, autoria preexistente, contratos e configurações pessoais.

## Escopo
Documentação, templates, prompts portáveis e verificação estrutural em Python.
A adoção ocorre nos dois repositórios alric, cada um utilizável em clone isolado.

## Fora do escopo
Instalação de ferramentas, modelos, credenciais, MCP, permissões globais,
publicação, alteração de regras remotas e execução de workloads em cloud.

## Conclusão
Ver [acceptance.md](acceptance.md). Configuração local validada não comprova
que uma sessão de cada produto carregou e obedeceu às instruções.
