# Princípios de engenharia

1. Requisitos definem o resultado desejado; código, testes e workflows mostram o
   comportamento atual. Resolva divergências explicitamente, sem reescrever
   requisitos para justificar a implementação.
2. Mantenha regras do produto em suas fontes executáveis e documentação canônica.
   Os arquivos de agentes apontam para elas.
3. Use a menor mudança que resolva o pedido; preserve trabalho alheio e escopo.
4. Explicite hipóteses relevantes e valide as que possam alterar o resultado.
5. Verificação deve ser proporcional ao risco. Teste comportamento e falhas reais;
   correção de texto não exige testes que apenas repetem o texto.
6. Não neutralize um gate para obter sucesso. Falha, ausência e não execução
   são estados diferentes; nenhum deles equivale a aprovação.
7. Preserve os limites de autoridade da [matriz](CAPABILITY-MATRIX.md).
8. Promova descobertas recorrentes para teste, documentação ou playbook na
   fonte adequada; evite acumular histórico nos arquivos carregados sempre.

Uma entrega registra o que mudou, critérios verificados, resultados e limites.
Revisão independente é exigida quando o risco descrito no
[workflow](WORKFLOW.md) justificar; auto-revisão deve ser identificada como tal.
