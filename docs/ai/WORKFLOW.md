# Fluxo de trabalho

## Escolha a profundidade

| Mudança | Registro e verificação |
| --- | --- |
| Pequena e reversível: texto, instrução ou ajuste localizado sem alterar contrato | Objetivo, diff e verificação relevante na resposta/PR |
| Não trivial: comportamento, múltiplos componentes ou nova automação | Pasta em specs com requisito, aceite, plano, tasks e evidência |
| Risco alto: publicação, assinatura, IAM, certificados, gates, contrato ou compatibilidade | Fluxo completo, negativos, rollback e revisão em contexto independente antes de integrar |

Se houver dúvida, registre um plano curto e avance no trabalho reversível.
O pedido para implementar autoriza elaborar spec e plano; não cria uma etapa
obrigatória de aprovação humana de cada documento.

## Executar

1. Leia [AGENTS.md](../../AGENTS.md) e o [mapa do projeto](PROJECT.md).
   Confira diretório, branch, diff e instruções locais; preserve mudanças existentes.
2. Selecione a spec indicada na tarefa. Não deduza a spec ativa pela data;
   se não houver uma correspondente, crie uma com o [template](../../specs/_template/spec.md).
3. Registre requisitos observáveis e critérios de aceite antes da implementação.
   Pesquise apenas código, testes e documentação necessários.
4. Planeje sequência, arquivos, riscos e rollback. Cada task referencia um
   critério de aceite e tem resultado verificável.
5. Implemente dentro do escopo. Registre mudança de estratégia no plano;
   mudança de requisito precisa ser explícita e alinhada ao pedido.
6. Execute os comandos relevantes do mapa. Registre saída/exit code, commit
   ou estado do diff, ambiente e limitações em evidence.md.
7. Revise diff versus spec e aceite. Em risco alto, peça revisão em nova sessão
   ou outra ferramenta usando o [prompt](../../prompts/independent-review.md).
   A mesma sessão relendo seu trabalho conta como auto-revisão.
8. Corrija achados, revalide a área afetada e registre o resultado final.
   PASS exige observação; FAIL, NOT RUN e BLOCKED não são PASS.
9. Ao trocar de ferramenta, use [handoff](../../prompts/handoff.md).
   Ao aprender algo recorrente, atualize a fonte canônica correspondente.

## Usar as três ferramentas

Os papéis são intercambiáveis. Por exemplo: Codex implementa; Claude Code
revisa em uma sessão nova; Copilot auxilia edições e revisão no IDE/PR.
Compartilhe spec, diff e evidência, sem depender de memórias de conversas.

Mantenha um único editor por árvore de trabalho. Para trabalho simultâneo,
use branches e worktrees separados, com tasks independentes e dono definido.
Subagentes só são usados quando solicitados pelo usuário; alternar ferramentas
sequencialmente já permite revisão independente.

## Criar uma spec

Na raiz do repo, escolha um identificador descritivo e um destino novo:

```sh
SPEC_ID=2026-09-12-ajuste-do-contrato
test ! -e "specs/$SPEC_ID" && cp -R specs/_template "specs/$SPEC_ID"
```

Preencha spec.md e acceptance.md primeiro. Os templates são auxílio, não
evidência de trabalho concluído. O validador verifica estrutura e links;
a qualidade dos requisitos e a execução dos aceites exigem revisão e testes.
