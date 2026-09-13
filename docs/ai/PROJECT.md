# Contexto da biblioteca

## Fontes canônicas

| Tema | Fonte |
| --- | --- |
| APIs, consumo e validação local | [README](../../README.md) |
| Inputs, artifacts, retenção e limites de confiança | [Contrato Apko/OCI](../apko-contract.md) |
| Regras de adoção e administração | [Governança](../governance.md) |
| Executores e CI | [.github/workflows](../../.github/workflows/) |
| Instalação governada do Trivy | [Action composta](../../actions/setup-trivy/action.yml) |
| Invariantes executáveis | [check_contracts.py](../../scripts/check_contracts.py) |
| Regressões | [test_contracts.py](../../tests/test_contracts.py) |

O checkout dos executores lê o consumidor, que fornece manifests, scripts e
testes. Catálogo, AWS, publicação e promoção pertencem ao produto consumidor.
Não acrescente comandos livres como inputs, secrets herdados nem permissões
de publicação aos executores de validação.

## Validar a partir da raiz

Com Python e PyYAML na versão indicada pelo README:

```sh
python3 -B -m unittest discover -s tests -v
python3 -B scripts/check_contracts.py
python3 -B tools/check_ai_context.py
actionlint .github/workflows/*.yml
```

O CI também instala Trivy de verdade no smoke-trivy. Verificação offline
não substitui esse smoke nem um teste real no consumidor.

Mudanças de paths, inputs, artifacts, retenção, ferramentas ou permissões
exigem verificar o consumidor. Mudança incompatível preserva a API anterior
durante a migração. Observe a sequência de publicação da action e adoção do SHA
descrita no README. O consumidor fixado por SHA não adota a main automaticamente.

## Manter este contexto

Não duplique pins e valores de contrato nas instruções de IA. Atualize a fonte
e seus testes. As políticas versionadas descrevem o estado desejado; estado
remoto exige verificação própria. O validador em tools é tooling de desenvolvimento.
