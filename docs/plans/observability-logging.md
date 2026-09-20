# 手動テスト向けログ基盤の導入

状態: `[x]` 実装完了(ブランチ`feat/observability-logging`、PR未作成・CI未確認)。
1〜3全てコミット済み。frontend側はサンドボックスで`npx tsc --noEmit`/`npx eslint`のみ確認
(`npm run test`はサンドボックスのネイティブバイナリ不整合で未実行)。backend/workerは
`py_compile`のみ(NOT VERIFIED)。実際のpytest/vitest実行結果はGitHub Actions CIで確認予定。

## 背景

現状、ログの仕組みは以下のみ。

- backend API（`app/main.py`ほか）: ログ出力なし
- worker（`app/market_data/worker/__main__.py`）: 標準`logging.basicConfig`でstdoutのみ
- frontend（React）: エラーを捕捉・記録する仕組みなし

一方 `requirements.txt` には構造化ログ用として `structlog` が既に依存に入っているが、
どこからも使われていない（`app/`配下でimportされていない）。

目的は、手動テスト中に不具合が発生した際に後から追える記録を残すこと。
新しいライブラリを追加するのではなく、既存の`structlog`を実際に配線する。

出力先はファイル（`logs/`配下、ローテーション付き）。対象範囲はbackend API・
frontend・workerの3つ。

### 重要な制約（この計画全体に適用）

OANDA/Binanceの認証情報は`app/services/secrets.py`の`LocalEncryptedSecretStore`で
暗号化保管され、DB上には`secret_ref`（`local-encrypted://<uuid>`という参照文字列の
み）が入る。APIスキーマ（`app/schemas/connections.py`）では認証情報を`SecretStr`で
受けており、`revealed_credentials()`を明示的に呼ばない限り生の値は取得されない。

この設計を壊さないよう、ログ基盤は以下を厳守する。

- リクエスト/レスポンスの**ボディをログに含めない**（method/path/status/所要時間/
  相関IDのみ）
- 構造化ログのイベントdictに対し、`api_key` / `secret` / `token` / `password` /
  `credential` / `authorization` 等のキー名を機械的にマスクする redaction
  processorを通す（手書きのログ呼び出しに対する多重防御）
- `secret_ref`（暗号化ファイルへの参照文字列であり秘密情報そのものではない）は
  ログ対象外にはしないが、生の認証情報・Fernet鍵・`.env`の値は一切ログに出さない

---

## 1. backend共通ログ基盤 `[x]` 実装済み(コミット済み、CI未確認)

### 実装目的
structlogを実際に配線し、FastAPIアプリの例外・主要処理をファイルに残す。

### 変更対象
`app/main.py`（起動時にログ初期化・リクエストロギングミドルウェア追加）、
`app/core/config.py`（ログ出力先・レベルの設定項目を追加）。

### 新規作成対象
`app/core/logging.py`: structlog設定、redaction processor、
`RotatingFileHandler`（サイズベース、例: 10MB×5世代）を使ったファイル出力の初期化。
出力先は`logs/backend.log`（`settings`から変更可能）。

### データフロー
アプリ内の各所 → structlogロガー → redaction processor → stdout + `logs/backend.log`。

### 依存関係
`structlog`（既存依存、追加インストール不要）。標準ライブラリの
`logging.handlers.RotatingFileHandler`。

### 実装順序
1. `app/core/logging.py`でstructlog設定とredaction processorを実装し、
   redaction処理の単体テストを先に書く（既知のキー名が正しくマスクされるか）。
2. `app/core/config.py`にログ関連設定（出力先ディレクトリ、レベル、ローテーション
   サイズ）を追加。
3. `app/main.py`起動時にログ初期化を呼び出し、リクエストロギングミドルウェアを
   追加（method/path/status/所要時間/相関IDのみ、ボディは含めない）。
4. `.gitignore`に`logs/`を追加。

### テスト方法
redaction processorの単体テスト、FastAPI TestClientで`/health`等にリクエストし
ログファイルに1行出力されることを確認する統合テスト。

### 想定リスク
中。redactionのキー名網羅が不十分だと秘密情報が漏れる可能性があるため、
`app/schemas/connections.py`の`credentials`関連フィールド名を洗い出した上で
リストを作る。ミドルウェアがボディを見ない設計にすることが最大の防御線。

---

## 2. workerプロセスのログ統一 `[x]` 実装済み(コミット済み、CI未確認)

### 実装目的
`app/market_data/worker/__main__.py`の`logging.basicConfig`をbackendと同じ
structlog基盤に統一し、`logs/worker.log`にも残す。

### 変更対象
`app/market_data/worker/__main__.py`、`app/market_data/worker/runner.py`の
ロガー初期化部分。

### 新規作成対象
なし（1.で作った`app/core/logging.py`を再利用）。

### 実装順序
1. `logging.basicConfig`呼び出しを`app/core/logging.py`の初期化関数に置き換える。
2. 出力先を`logs/worker.log`に設定する。
3. worker起動時にログファイルへ実際に書き込まれることをローカルで確認する。

### テスト方法
ローカルでworkerを起動し、`logs/worker.log`に構造化ログが出力されることを目視確認。

### 想定リスク
低。ログの出力先・フォーマットが変わるだけで、worker自体の挙動（取引所への
接続・データ取り込み）には触れない。

---

## 3. frontendのエラー捕捉 `[x]` 実装済み(案A、コミット済み、CI未確認)

### 実装目的
手動テスト中にUIで起きたJSエラー・未処理のPromise rejectionを記録し、
バックエンドのログと合わせて後から追えるようにする。

### 設計上の決定（確定: 案A）

ブラウザはファイルシステムに直接書き込めないため、`POST /api/v1/client-logs`
を新設し、frontendの`window.onerror` / `unhandledrejection` / React Error
Boundaryで捕捉した情報（メッセージ・スタック・発生画面・timestamp）をPOSTし、
backendが`logs/frontend.log`に書き込む。ペイロードに認証情報が乗らないよう
送信項目を固定のホワイトリスト形式にする。

### 変更対象
`app/api/routes/`に新規ルート追加（`client_logs.py`想定）、`frontend/src`に
エラー捕捉ユーティリティを追加。

### 新規作成対象
`app/api/routes/client_logs.py`（新規エンドポイント）、
`frontend/src/lib/errorReporting.ts`（想定、既存の`frontend/src/lib/`配下の
構成に合わせる）。

### 実装順序
1. 既存API（`app/api/routes/connections.py`等）のスコープ設計（ワークスペース
   単位か、認証の要否）を確認し、`client-logs`エンドポイントのスコープ方針を
   決める。
2. `POST /api/v1/client-logs`を実装。受け取るフィールドをホワイトリスト化し、
   ペイロードサイズに上限を設ける。認証情報らしき値が含まれていないか
   redaction processorを通した上で`logs/frontend.log`に書き込む。
3. frontend側に`window.onerror` / `unhandledrejection`ハンドラと、
   React Error Boundaryを追加し、上記エンドポイントへ送信する。
4. ローカルで意図的にエラーを起こし、`logs/frontend.log`に記録されることを
   確認する。

### テスト方法
frontend側はエラーハンドラの単体テスト（fetchをモックして呼び出し確認）。
backend側は1.と同様の統合テストに加え、ホワイトリスト外のフィールドが
無視される・ペイロード上限超過時に拒否されることを確認するテストを追加する。

### 想定リスク
中。新規エンドポイントのスコープ設計（誰でも送信できてよいか、ワークスペース
単位にするか）を既存API規約に合わせて決める必要がある。

---

## 全体の実装順序

1. backend共通ログ基盤（redaction processor含む）
2. workerのログ統一
3. frontendのエラー捕捉（案A/B確定後）

1・2は依存が少なく先に進められる。3は設計決定（案A/B）を待ってから着手する。
