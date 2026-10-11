# AI System Trading ドキュメント

価格の動きを見て、決まったルールでペーパートレード(売買の練習)を自動で行い、成績と理由を画面で確かめられるシステムのドキュメントです。
**本物のお金・本物の注文は扱いません。**

読む人に合わせて、入口を選んでください。

| 読む人 | 入口 |
|---|---|
| まず全体を知りたい | [システムの全体像](overview/01-system-overview.md) → [トレードロジックの説明](overview/02-trading-logic.md) |
| 開発する | [システム仕様書](design/system-specification.md)、[APIリファレンス](design/api/reference.md)、[DBリファレンス](design/database/schema-reference.md) |
| 判断の背景を知りたい | [設計判断(ADR)](decisions/0002-binance-cross-margin-adoption.md)、[計画書の一覧](plans/README.md) |

```{toctree}
:maxdepth: 2
:caption: はじめに(非エンジニア向け)

overview/01-system-overview
overview/02-trading-logic
```

```{toctree}
:glob:
:maxdepth: 2
:caption: 開発者向け仕様

design/system-specification
design/api/reference
design/database/schema-reference
design/modules/*
design/database/durable-market-data-worker
quality/definition-of-done
```

```{toctree}
:glob:
:maxdepth: 1
:caption: アーキテクチャと設計判断

architecture/*
decisions/*
requirements/README
```

```{toctree}
:glob:
:maxdepth: 1
:caption: 計画書と検証メモ

plans/README
plans/*
```

```{toctree}
:glob:
:maxdepth: 1
:caption: 知見(過去の不具合・環境依存)

knowledge/*
```
