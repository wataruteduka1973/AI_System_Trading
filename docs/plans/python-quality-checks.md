# Python整形・型検査の導入

## 目的と対象

CIのRuff整形エラー6ファイルを解消し、未設定だったPython型検査をローカル/CI共通にする。
公開API・DB・取引所への接続方法は変更しない。既存の未コミットWorker実装を保持する。

## 実装順序

1. 既存6ファイルをRuffで整形。処理内容が変わらないことを差分で確認する。
2. mypyを開発依存に追加。app/src/scriptsを対象に、型注釈済みの契約と未注釈関数内部を検査する。
3. 検出された型の不一致を修正し、CIに同じ検査を追加する。
4. lint/format/typecheck、既存テスト、package buildを実施し、READMEへ手順を記載する。

型定義がない外部SDKはそのmoduleだけを明示する。全体のignore_errorsや
follow_imports=skipでアプリケーションのエラーを隠さない。
テスト・migration自体の厳格な型付けは今回の対象外（実行テスト/整形/lintは継続）。
リスクはORM/JSON/SDK境界の型と実際の返り値のずれ。挙動を維持する明示的な変換・型の絞込みで対処する。

## 実装結果

- 6ファイルの整形不一致を解消。既存Worker変更は保持した。
- Ruffを検証済み0.16.3に固定し、ローカルとCIのformatterバージョンを合わせた。
- mypyを開発依存へ追加。app/src/scripts計43ファイルを対象にし、未注釈関数内部も検査する。
  strictモードによる全関数への注釈強制ではない。型定義のない `binance.*` と
  `oandapyV20.*` のimportだけを例外にし、アプリのエラーは抑制しない。
- `types-requests` を追加。ローカルとCIは `python -m mypy` で同一設定を使う。
  CIでは `--platform win32` も実行してWindows用launcherの分岐を確認する。
- ORM Rowを宣言どおりtupleに変換、upsertのboolean戻り値を明示、DB/JSON境界の型を絞った。
  非有限の精度値と破損した進捗reportを明示的に拒否し、回帰試験を追加。
  APIのpath/schema、DB migration、運用データ、外部注文には変更なし。
- launcherは型検査が認識できる `sys.platform` の分岐へ整理。非Windowsでのキー操作拒否と
  SIGTERM経路を追加試験した。実サービスは起動/停止していない。

## 検証上の注意

- 初回の全体試験では、mypyと並行実行中の1年分1分足検証が10.73秒となり、10秒の基準を超過。
  合格基準は緩和せず、重い検査を並行させず再実行したところ、同じ性能試験も通過した。
  本試験は環境負荷に依存する性能ゲートであり、高負荷時の処理時間には余裕が少ない。
- SDK/Starletteの非推奨警告、GitHub上の実CI結果、独立Worker切替後の実地確認は別課題。

## 最終検証（2026-08-31）

- Ruff lint/全体format成功（95ファイル）。変更差分を確認し、既存の未コミット作業を保持。
- mypy 2.3.1: Linux/win32両設定で43ファイル成功。型の不一致を与える検出試験も実施。
- backend185件成功（専用PostgreSQL18.6の48件を含む）。今回15件の回帰テストを追加。
  運用DBは使用せず、専用DBを試験後に停止した。
- frontend11件、ESLint、TypeScript/Vite build、Python wheel/sdist build成功。
- UI変更なし。外部API実通信・実サーバー起動操作なし。GitHub上のCI実行結果はNOT VERIFIED。
- ローカルのmypyは試験用フォルダーへインストールして確認。利用者の仮想環境には
  READMEの `python -m pip install -e ".[dev]"` で開発ツールを反映する。
