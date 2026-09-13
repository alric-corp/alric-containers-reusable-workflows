# Trabalhar com Codex, Claude Code e Copilot

Abra **a raiz Git deste repositório** na ferramenta escolhida. O contexto é
versionado aqui e funciona em clone isolado; a pasta pai distroless-solution
é um workspace com vários repos, não um repositório único.

## Entradas e fontes

| Camada | Arquivo | Benefício |
| --- | --- | --- |
| Acordo compartilhado | [AGENTS.md](../../AGENTS.md) | Contexto consistente entre ferramentas |
| Claude Code | [CLAUDE.md](../../CLAUDE.md) | Importa AGENTS.md com @AGENTS.md |
| Copilot | [copilot-instructions.md](../../.github/copilot-instructions.md) | Aponta para contexto e fontes do projeto |
| Mapa técnico | [PROJECT.md](PROJECT.md) | Menos buscas repetidas e comandos presumidos |
| Princípios e limites | [Constituição](CONSTITUTION.md), [capacidade](CAPABILITY-MATRIX.md) | Decisões e autoridade explícitas |
| Execução | [WORKFLOW.md](WORKFLOW.md) | Rigor proporcional à mudança |
| Estado da tarefa | [Templates](../../specs/_template/) | Requisitos, aceite e continuidade |
| Procedimento de domínio | [Playbook](../../playbooks/platform-change.md) | Verificação orientada ao risco |

O contexto comum tem uma fonte por repo. Os dois repositórios são autônomos:
uma alteração de processo comum deve ser revisada em ambos, sem depender de
links para diretórios irmãos. O material original em analysis inspirou esta
adaptação; não é uma dependência de execução.

## Começar uma tarefa

No chat de qualquer ferramenta:

> Leia AGENTS.md e docs/ai/PROJECT.md. Quero [resultado]. Para mudança não
> trivial, crie uma spec, defina aceite e plano; depois implemente e verifique.
> Registre evidências e limitações.

Você pode anexar ou colar um dos [prompts](../../prompts/):
pesquisa e plano, implementação, revisão independente ou handoff. Esses arquivos
são textos portáveis, não comandos slash instalados. O prompt de pesquisa/plano
termina no planejamento por opção explícita; o pedido acima inclui implementação.

Para retomar:

> Leia AGENTS.md e specs/[id]/handoff.md. Confira o diff atual e continue a
> tarefa indicada, preservando os requisitos e resultados já verificados.

Mantenha o contexto de cada mudança em sua spec. Não acumule relatos de sessões
em AGENTS.md e não presuma que uma ferramenta conhece a conversa da outra.

## Conferir instalação e carregamento

1. Na raiz do repo, execute `python3 -B tools/check_ai_context.py`.
   Ele valida arquivos exigidos, import do Claude e links Markdown locais,
   sem chamadas de IA ou rede. Não valida anchors, URLs externas ou qualidade
   semântica dos requisitos.
2. Abra uma **nova sessão** de cada ferramenta. Peça que identifique o repo,
   a fonte principal de instruções e os comandos adequados à tarefa, citando
   os arquivos consultados. Compare com [PROJECT.md](PROJECT.md).
3. No Claude Code, confira CLAUDE.md nos arquivos de memória com `/context`.
   No Copilot Chat, confira copilot-instructions.md nas referências da resposta.
   Se uma referência não for carregada pela interface, anexe AGENTS.md e o
   mapa explicitamente. Suporte varia entre IDE, CLI, cloud e revisão.
4. Faça uma tarefa pequena e confira o diff e a verificação. Registre a
   ferramenta/versão e o que foi observado ao avaliar a adoção.

A configuração não fixa modelo, instala extensão nem muda permissões pessoais.
Instruções em Markdown orientam os agentes; testes/CI e permissões da ferramenta
continuam responsáveis pelos controles executáveis. O CI existente descobre
os testes do validador, inclusive em alterações só de documentação, depois que
esses arquivos forem versionados. Uma execução local não prova CI remoto verde.

## Avaliar o benefício

Nas próximas três mudanças, observe: foi necessário reexplicar o projeto?
Os critérios foram definidos antes do código? Houve erro de comando evitável?
Outra ferramenta conseguiu retomar só com spec e handoff? A evidência permitiu
conferir a conclusão? Registre os ajustes úteis nas fontes correspondentes.

## Fontes oficiais

Consultadas em 11/09/2026:

- [Codex — descoberta de AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md).
- [Claude Code — memória e import de AGENTS.md](https://code.claude.com/docs/en/memory).
- [Copilot — instruções de repositório no IDE](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions-in-your-ide/add-repository-instructions-in-your-ide).
- [Copilot — suporte por ambiente](https://docs.github.com/en/copilot/reference/custom-instructions-support).

As entradas seguem esses mecanismos documentados. O fluxo com spec, revisão
e handoff é uma escolha de engenharia deste projeto.
