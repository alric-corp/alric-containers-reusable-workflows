# Capacidade e autorização

O pedido do usuário e as permissões efetivas delimitam a execução. Esta matriz
não concede acesso nem substitui sandbox, políticas da organização ou controles
das ferramentas. Autorização já dada vale durante a tarefa.

| Classe | Exemplos neste cenário | Como agir |
| --- | --- | --- |
| LOCAL-EXECUTE | Ler, editar, criar spec, testar e preparar diff/PR | Avançar dentro do pedido; decisões reversíveis não exigem nova confirmação |
| SANDBOX-EXECUTE | Build local; testes na conta pessoal AWS | Executar quando o escopo incluir isso; registrar ambiente, custo relevante e resultado |
| EXTERNAL-WRITE | Publicar PR/issue, enviar mensagem, alterar configuração remota, publicar imagem ou mover tag | Usar autorização existente para a ação; caso ausente, preparar resultado revisável e solicitar somente o passo externo necessário |
| EXTERNAL-DECISION | Aceites corporativos de IAM, PKI, rede, AppSec ou exceções | Identificar owner e preparar artefato; capacidade técnica não prova autoridade |
| FORBIDDEN | Expor segredos, inventar evidência, burlar controles para obter aprovação | Não executar |

Se a ferramenta bloquear um comando, explique o bloqueio e use o mecanismo
normal de aprovação quando necessário. Não mude de ferramenta para contorná-lo.
Um teste pessoal prova apenas seu escopo; aceites corporativos permanecem
separados. Trabalho local pode terminar com dependências externas documentadas,
sem declarar essas dependências aplicadas.

## Decisão do usuário para o sandbox pessoal

`enforce_admins` permanece desligado neste sandbox por decisão explícita do
usuário, que trabalha sozinho. As aprovações de `vigcf` serviram somente para
testar a funcionalidade. Não habilitar esse controle nem tratar sua ausência
como regressão do sandbox nas próximas reconciliações.

A exigência de `enforce_admins` fica reservada ao cenário corporativo
(P0-03). A adequação das frases de governança na documentação do produto fica
para P1-10. Esta decisão não altera gates de segurança, testes ou contratos e
não declara o ambiente corporativo configurado. Nenhuma configuração remota
foi alterada nesta reconciliação local.
