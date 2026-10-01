# Pilha XML/criptográfica — DEP-01

Versões fixadas juntas no SHA `a011aa1`:

| Pacote | Antes | Depois |
|---|---|---|
| SignXML | 3.2.2 | 5.1.0 |
| cryptography | >=41,<42 (41.0.7) | 50.0.1 |
| pyOpenSSL | >=23,<24 (23.3.0) | 26.4.0 |
| lxml | 5.3.0 | 6.1.3 |

O build de runtime resolveu as dependências sem os tetos antigos;
`pip check` não apontou inconsistência. Referências primárias:
[SignXML](https://github.com/XML-Security/signxml/releases),
[cryptography 50.0.1](https://pypi.org/project/cryptography/50.0.1/),
[pyOpenSSL 26.4.0](https://pypi.org/project/pyOpenSSL/26.4.0/),
[lxml 6.1.3](https://pypi.org/project/lxml/6.1.3/).
Advisories de verificadores HMAC antigos não comprovam que esse caminho era
utilizado pela aplicação; a assinatura de DPS é RSA, não HMAC.

## Matriz de compatibilidade

| Perfil | Prova local | Resultado/limite |
|---|---|---|
| DPS Nacional v1.01 | XML real do builder, UTF-8 e declaração XML, `#Id` de infDPS, namespaces sem prefixo | Verificado com certificado sintético |
| XMLDSig Nacional | RSA-SHA256, digest SHA256, enveloped-signature e C14N exclusiva | Sign/verify passou; alteração de descrição/valor invalida assinatura |
| XSD | Esquemas versionados em `app/schemas/nfse_nacional` | Passou com a exceção TSSerieDPS preexistente, sem nova dispensa de validação |
| mTLS | requests + servidor TLS local que exige certificado e CA sintética | Dois certificados sucessivos aceitos; sem certificado/CA errada recusados; PEM removidos |
| PFX/Fernet | Certificado válido, vencido, senha errada, cache vencendo e dois processos | Validado; PFX e senha antigos continuam decifráveis |
| Municipal legado | RSA-SHA1/C14N inclusiva originais | Biblioteca recusa o perfil; falha local explícita. Nenhum bypass de validação ou mudança de algoritmo |
| Fisco/ICP-Brasil real | Homologação restrita autorizada | **NÃO EXECUTADO**: não foram usados certificados da empresa nem APIs fiscais reais |

## Scanner revisado

`pip-audit 2.10.0` foi instalado em venv separado e examinou somente o
site-packages da imagem runtime, sem alterar suas dependências:

```text
/audit/bin/python -m pip_audit --path /usr/local/lib/python3.12/site-packages -f json
```

[Saída JSON](../validacao/fase-05/pip-audit.json): nenhum advisory nos quatro
pacotes atualizados. O scanner retornou código 1 e duas entradas para o mesmo
advisory de **zeep 4.3.1**, `PYSEC-2026-2323` / `CVE-2026-58501` /
`GHSA-4cc2-g9w2-fhf6`, correção indicada **4.3.3**.

Esse achado é residual na integração Multiportal (`services/multiportal.py`),
que importa Zeep e carrega o WSDL configurado. O advisory descreve referências
externas transitivas de WSDL/XSD e ineficácia de `forbid_external` nas versões
afetadas. Não foi comprovada exploração nem entrada de WSDL arbitrário por
usuário comum. O serviço atual usa `Settings(strict=False, xml_huge_tree=True)`;
não alegamos que uma flag de bloqueio esteja funcionando ali.

A correção do Zeep e a revisão de imports do WSDL Multiportal ficam como
pendência específica, com regressões próprias dessa integração. O scanner
global **não está limpo**; DEP-01 fica parcial até essa revisão e a homologação
fiscal restrita. Não foi adicionado ignore/waiver ao scanner.
