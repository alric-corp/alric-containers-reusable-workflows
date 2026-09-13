# Mudança de plataforma

Use em alterações de workflow, ferramentas, contrato, composição de imagem ou
gates. Leia o [mapa](../docs/ai/PROJECT.md) e siga o
[workflow](../docs/ai/WORKFLOW.md) no nível de risco correspondente.

1. Identifique a fonte executável da regra e os consumidores afetados.
2. Registre na spec comportamento atual, mudança desejada e invariantes.
3. Liste riscos: compatibilidade, permissões, artifacts, assinatura, scan e retenção.
4. Defina aceites positivos e negativos, ambiente de prova e rollback.
5. Implemente a menor mudança que atenda aos critérios.
6. Execute checks locais e a integração pertinente; registre o que não rodou.
7. Para alterações entre repos, identifique commits de biblioteca e consumidor
   separadamente; o checkout no SHA publicado continua sendo a referência.
8. Faça revisão independente quando o risco justificar e resolva achados.
9. Atualize evidência com comandos, resultados e identidades observadas.

Não altere requisitos para contornar falha; corrija a implementação ou registre
a decisão necessária. Não atualize tags, políticas remotas ou ambiente cloud
sem o escopo autorizado na [matriz](../docs/ai/CAPABILITY-MATRIX.md).
