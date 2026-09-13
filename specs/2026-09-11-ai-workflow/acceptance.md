# ACCEPTANCE — 2026-09-11-ai-workflow

| ID | Critério obrigatório | Verificação |
| --- | --- | --- |
| A01 | Três entradas convergem para AGENTS.md; referências locais resolvem dentro do repo | Validador offline e revisão dos adaptadores |
| A02 | Workflow proporcional, templates e passagem entre ferramentas documentados | Revisão dos documentos e exemplo de uso |
| A03 | Comandos e mapa do projeto correspondem ao código atual | Conferir README, testes e workflows |
| A04 | Regressões de integração do contexto são descobertas pelo CI existente | Suíte de testes existente |
| A05 | Contratos, pins, permissões e atribuição existente são preservados | Revisão do diff |
| N01 | Arquivo obrigatório ausente, import quebrado e link inválido falham | Testes negativos do validador |

## Verificação posterior na interface
Confirmar o carregamento em uma nova sessão de Codex, Claude Code e Copilot,
conforme o guia. Registrar como não executado nesta entrega se não observado.
Essa confirmação não é substituída pelo validador estrutural.
