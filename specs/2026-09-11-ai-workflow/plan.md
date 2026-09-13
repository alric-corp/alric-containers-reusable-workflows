# PLAN — 2026-09-11-ai-workflow

## Estado observado
O workspace pai não é repositório Git. Os dois projetos alric possuem CI
próprio e contratos de integração; o material em analysis é referência.
A biblioteca e o consumidor devem continuar funcionando em clones isolados.

## Execução
1. Adaptar AGENTS.md ao projeto e preservar instruções existentes.
2. Adicionar adaptadores Claude/Copilot e guia de uso com fontes oficiais.
3. Criar constituição, matriz de capacidade e workflow proporcionais ao risco.
4. Adicionar templates, prompts e playbook com fontes do projeto.
5. Verificar estrutura e referências usando Python sem dependências adicionais;
   incluir regressões na descoberta de testes existente.
6. Executar checks relevantes e registrar resultados e limites.

## Decisões
Instruções de domínio têm uma fonte por repositório. Os adaptadores não
replicam regras. Os dois clones têm seu próprio contexto, sem symlinks externos.
Prompts Markdown funcionam como texto/anexo; não presumem comandos slash.
Não alterar filtros de publicação nem introduzir serviços para essa configuração.

## Riscos e rollback
Instruções extensas podem consumir contexto: manter AGENTS.md curto e carregar
material por necessidade. Instruções não são enforcement de permissões.
Rollback: reverter os arquivos desta mudança, preservando mudanças do usuário.

## Autoridade
LOCAL-EXECUTE: edição e testes autorizados por este pedido.
Nenhuma operação externa é necessária para preparar a configuração.
