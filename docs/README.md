# ドキュメントの案内

読む人に合わせて、入口を選んでください。

## まず全体を知りたい(非エンジニア向け)

| 文書 | 内容 |
|---|---|
| [overview/01-system-overview.md](overview/01-system-overview.md) | このシステムは何をしているか。全体の流れ、部品、安全のしくみ、画面の見方、検証結果とその限界、用語集 |
| [overview/02-trading-logic.md](overview/02-trading-logic.md) | 売買のルール。いつ買い・損切り・決済するか、どれだけ買うか、リスク判定、取引停止、検証の道のり |

## 開発するとき(開発者・Agent向け)

| 文書 | 内容 |
|---|---|
| [design/system-specification.md](design/system-specification.md) | システム仕様書。プロセス構成、モジュール、ER 図、取引パイプライン、Risk Gate の式、halt、注文・台帳、通知、バックテスト、認証、設定、テスト |
| [design/api/reference.md](design/api/reference.md) | **API リファレンス(自動生成)**。全エンドポイントの権限・パラメータ・応答 |
| [design/database/schema-reference.md](design/database/schema-reference.md) | **DB リファレンス(自動生成)**。全テーブルの列・キー・索引 |
| [quality/definition-of-done.md](quality/definition-of-done.md) | 完了の条件 |

自動生成の2つは直接編集しません。ルート・スキーマ・モデルを変えたら、次で再生成します
(`tests/test_reference_docs.py` が、古いと失敗します)。

```bash
python scripts/generate_reference_docs.py
```

## 判断の背景を知りたいとき

| 場所 | 内容 |
|---|---|
| [architecture/](architecture/) | 現在の構造と、長期のロードマップ・承認ゲート |
| [decisions/](decisions/) | ADR(重要な設計判断とその理由) |
| [plans/README.md](plans/README.md) | 機能ごとの計画書と、検証メモ(バックテスト・ウォークフォワードなど)の索引 |
| [knowledge/](knowledge/) | 過去の不具合・環境依存・ライブラリの罠。同じ間違いを避けるための記録 |
| [requirements/](requirements/) | 要件(現在のスコープの扱い) |
| [concept/](concept/) | 初版の構想(現在のスコープ外) |

## サイトとして読む(Sphinx / GitHub Pages)

このディレクトリは、Sphinx(MyST で Markdown を読む)でサイトにまとめられます。`main` へのマージで、
GitHub Pages(<https://wataruteduka1973.github.io/AI_System_Trading/>)へ公開されます
(`.github/workflows/docs.yml`)。構成は [index.md](index.md)(トップ)と [conf.py](conf.py) にあります。

```bash
python -m pip install -r docs/requirements.txt
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

- 警告はエラーとして扱います(壊れたリンク・どの目次にも入っていない文書を見逃さないため)。
  新しい文書を `docs/` に足したら、[index.md](index.md) の目次(`toctree`)にも載せてください
  (`plans/` `decisions/` `architecture/` `knowledge/` は、ファイルを置くだけで載ります)。
- `docs/concept/`(初版の構想、現在のスコープ外)と、この `README.md` は、サイトには含めません。
- Mermaid の図は、ブラウザ側で描画されます。
