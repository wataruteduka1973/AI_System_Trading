# AI System Trading

AIモデルとテクニカル分析指標を利用し、複数の取引所にまたがる取引を管理するためのプロジェクトです。

> [!WARNING]
> 現在は開発初期段階です。実際の資金を使った取引には使用しないでください。

## 開発環境

- Python 3.13
- Node.js 22
- PostgreSQL 16以上
- Git

```powershell
python -m venv .venv313
.\.venv313\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

`.env.example`を`.env`へコピーし、ローカルPostgreSQLの接続情報を設定します。実際のパスワードやAPIキーはコミットしないでください。

```powershell
Copy-Item .env.example .env
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

`Copy-Item`だけではDB接続は完了しません。`.env`の`DATABASE_URL`にある`trade_bot_user`と`change-me`は例示値なので、pgAdminで実際に接続できるユーザー名とパスワードへ変更してください。まず既存環境を確認する場合は、次の形式で設定します。

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:実際のパスワード@localhost:5432/general_system_db
```

パスワードに`@`、`:`、`/`、`#`、`%`などが含まれる場合はURLエンコードが必要です。

セッションCookie署名鍵とローカル暗号化Secret Storeには、それぞれ別のランダム値を設定します。次のコマンドで値を生成し、表示された値を`.env`の`SESSION_SIGNING_SECRET`と`SECRET_ENCRYPTION_KEY`へ設定してください。値はコミット、チャット送信、スクリーンショット共有をしないでください。

```powershell
.\.venv313\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv313\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```dotenv
SESSION_SIGNING_SECRET=1つ目のコマンドで生成した値
SECRET_ENCRYPTION_KEY=2つ目のコマンドで生成した値
SECRET_STORE_PATH=.secrets
```

ログインには外部OIDC IdP(Auth0、Okta、Microsoft Entra ID等)が必要です。`.env`の`OIDC_ISSUER`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET`をそのIdPの値に設定し、`OIDC_REDIRECT_URI`(既定値`http://localhost:8000/api/v1/auth/callback`)をそのIdP側のRedirect URI許可リストへ登録してください。未設定の間は`/auth/login`が503を返します(認証以外のAPI動作確認には影響しません)。

FastAPIサーバーを起動する際には以下のコマンドで起動します。
```powershell
fastapi dev app/main.py
```

既に`postgresql_schema_v0.1.sql`を適用済みのDBでは、内容を確認したうえで`python -m alembic stamp head`を使い、同じDDLを再実行しないでください。

別のターミナルでフロントエンドを起動します。

```powershell
Set-Location frontend
npm install
npm run dev
```

- React: `http://localhost:5173`
- FastAPI docs: `http://localhost:8000/docs`
- API health: `http://localhost:8000/api/v1/health`
- DB health: `http://localhost:8000/api/v1/health/db`

### Windowsでまとめて起動・再起動

初回セットアップ後は、プロジェクト直下の **`start-local.bat` をダブルクリック**してください。
バックエンド・フロントエンド・市場データWorkerの3プロセスを1つのウィンドウで起動し、
準備ができたらブラウザーを開きます。

- 起動ウィンドウで **R**: API/画面だけを再起動（Enter不要）。市場データWorkerには触れないので、
  進行中の取得を止めずにバックエンド/フロントエンドのコード変更を反映できます。
- **A**: Worker含む全プロセスを停止して再起動。
- **Q** または **Ctrl+C**: この起動操作で開始したプロセスを停止して終了。
- ウィンドウの×や強制終了ではなく、Qで終了してください。
- `start-local.bat --check`: 環境・ポートだけ確認し、起動しません（DB接続は確認しません）。
- `start-local.bat --no-browser`: ブラウザーを自動で開かず起動します。

PostgreSQLはあらかじめ起動してください。依存関係のインストール、DBマイグレーション、
`.env`や資格情報の書き換えは自動では行いません。実行可能なプロジェクト内のPython 3.13環境を
`.venv` → `.venv313` の順で選択します。壊れた仮想環境はREADMEの手順で再作成してください。

8000/5173番ポートが使用中なら起動を中止します。以前の手動起動サーバーは、そのターミナルで
停止してからbatを起動してください。他のプロセスを勝手に終了したり、別ポートへ変更したりしません。
すべてローカルPCだけに公開します。バックエンドの自動リロードは使わず、Rで再起動します。
起動準備完了時の`Ready:`表示にWorkerの状態（稼働中/停止・要確認）も表示されます。
Workerが単独で終了した場合（例: 必須DBリビジョン未適用）は`[WARN]`が表示され、
API/画面は継続します。`A`キーでWorkerを再起動できます。

市場データの自動取得は独立したWorkerプロセスが行います（`docs/plans/durable-market-data-worker.md`）。
DBが必須のAlembicリビジョン（`20260831_0005`）に達していない場合、Workerだけが起動直後に
終了し、API・画面は通常どおり使えます（自動取得だけが止まります）。取得中/retry予定/blocked状態の
画面表示、Worker単独の再起動操作は今後の工程です。

### 初期API

主なエンドポイントです。全エンドポイントの一覧・スキーマは起動後に`/docs`(Swagger UI)を参照してください。

- `GET /api/v1/auth/login` — OIDCログイン開始(外部IdPへリダイレクト)
- `GET /api/v1/auth/callback` — OIDCコールバック(セッションCookie発行)
- `POST /api/v1/auth/logout` — ログアウト
- `GET /api/v1/auth/me` — 現在のユーザー情報と所属workspace一覧
- `POST /api/v1/auth/sessions/revoke` — 自分の全セッションを即時失効
- `GET /api/v1/workspaces` — 自分が所属するworkspace一覧
- `POST /api/v1/workspaces` — workspace作成(作成者がOwnerになる)
- `GET /api/v1/workspaces/{workspace_id}` — workspace詳細
- `GET /api/v1/workspaces/{workspace_id}/connections` — 接続一覧（秘密参照は返さない）
- `POST /api/v1/workspaces/{workspace_id}/connections` — 暗号化した認証情報で接続登録
- `POST /api/v1/workspaces/{workspace_id}/connections/{connection_id}/disable` — 接続無効化
- `POST /api/v1/workspaces/{workspace_id}/connections/{connection_id}/verify` — OANDA practice / Binance Spot Testnet 資格情報検証・口座同期
- `PUT /api/v1/workspaces/{workspace_id}/connections/{connection_id}/credentials` — 暗号化資格情報を置換して即時再検証
- `GET /api/v1/workspaces/{workspace_id}/trading-halts` — 発動中のtrading halt一覧
- `POST /api/v1/workspaces/{workspace_id}/trading-halts/{halt_id}/release` — halt解除(Ownerのみ)
- `GET /api/v1/exchanges` — 対応取引所一覧
- `GET /api/v1/markets` — 対応市場一覧

全APIエンドポイントは、`GET /api/v1/auth/login`からのOIDCログインで発行される`session` Cookie(HttpOnly、`SameSite=Strict`)による認証と、workspaceごとのOwner/Operator/Viewerロール(`user_membership`テーブル)による認可を要求します。フロントエンドはログイン後、自分が所属するworkspaceのみ一覧・操作できます。取引所認証情報は`.secrets/`へFernet暗号化して保存し、DBには`local-encrypted://...`形式の参照だけを保存します。

OANDA検証は公式practice APIの口座一覧、口座summary、USD/JPY instrumentを読取専用で取得します。口座IDは暗号化・ハッシュ・マスクして保存し、画面とAPIにはマスク値だけを返します。外部注文endpointは呼び出しません。

### 接続トラブルの確認順

1. `http://localhost:8000/api/v1/health`が開かなければFastAPIを起動する
2. APIは開くが`health/db`が503なら`.env`の`DATABASE_URL`を確認する
3. PostgreSQL側で`general_system_db`、接続ユーザー、パスワード、5432番ポートを確認する
4. 既にDDL適用済みなら、接続成功後に`python -m alembic stamp head`を実行する

## ローカルでの確認

プロジェクトのPython 3.13環境を有効にして、開発用ツールを更新します。

```powershell
python -m pip install -e ".[dev]"
```

```powershell
ruff check .
ruff format --check .
python -m mypy
python -m pytest
```

整形エラーは `ruff format .` で修正してから、上記チェックを再実行してください。
Ruffは検証済みの0.16.8に固定し、ローカルとCIで整形結果がずれないようにしています。
型検査は `pyproject.toml` で `app`・`scripts` を対象にしています。
型定義のないBinance/OANDA SDK以外のエラーは無効化しません。
Windows用分岐も確認する場合は `python -m mypy --platform win32` を実行します。
テストやmigration自身の厳格な型付けは対象外ですが、整形・lint・実行テストは継続します。

### Worker DB基盤の統合試験（開発者向け）

市場データWorker（`app/market_data/worker/`、`python -m app.market_data.worker`で起動）は
既存の取得経路（旧lifespanポーラー、BackgroundTasks即時実行）を置き換え済みです。
運用DB（`.env`の`DATABASE_URL`）へ追加migration `20260831_0005` を適用する前に、
`docs/plans/durable-market-data-worker.md` の切替手順を必ず確認してください。
未適用のDBに対してWorkerは候補処理を開始せず、安全なエラーを出して終了します。

試験は **空の専用PostgreSQLデータベース**（名前は `worker_test_` で始める）で実行します。
運用の `DATABASE_URL` は使わず、`WORKER_TEST_DATABASE_URL` に専用DBの接続先を設定して、
`ci.yml`の`worker-storage`ジョブと同じ順序で次を実行します（`test_migrations_roundtrip.py`が
最初に空のDBを受け取り空のDBへ戻す前提のため、この順序が必要です）。

```powershell
python -m pytest tests/test_migrations_roundtrip.py tests/test_notification_models.py tests/test_deliver_notifications.py tests/test_worker_leases_postgres.py tests/test_worker_lease_contracts.py tests/test_worker_pages.py tests/test_worker_runner_postgres.py
```

`test_worker_runner_postgres.py` は指定したDBの名前に `_runner_<乱数>` を付けた
使い捨てDBを自動作成・削除するため、指定するDB自体は空である必要はありますが、
テスト終了後に自動で片付きます。他のファイルはDDL作成とmigrationのdowngrade/upgradeを行い、
既存テーブルがあるDBを拒否します。再実行には新しい空の専用DBを用意してください
（このファイルはDBを自動削除しません）。
環境変数がない場合、PostgreSQL統合試験はskipされます（合格を意味しません）。
Worker本体の単体試験（signal処理、リビジョン確認、公平な巡回ロジック）は
`tests/test_worker_main.py` / `tests/test_worker_runner.py` にあり、DB不要で通常の
`python -m pytest` に含まれます。

### Notification Worker（開発者向け、Horizon5 Unit 8）

`app/notifications/worker/`（`python -m app.notifications.worker`で起動）は、`outbox_event`
テーブルをポーリングし汎用SMTPで通知を配信する単純なポーリングループです。市場データWorkerの
lease機構は使いません（通知送信は1回で完結する短い処理のため）。起動には`.env`の
`SMTP_HOST`/`SMTP_SENDER_ADDRESS`が必須で、未設定の場合はエラーメッセージを表示して
即座に終了します（`.env.example`参照）。現時点では`outbox_event`へ書き込むドメインイベント
発行元（trading_halt発動時の通知など）が実装されていないため、起動してもキューは常に空です
（基盤のみ実装済み、詳細は`docs/plans/horizon5-implementation-plan.md` Unit 8を参照）。
`scripts/start_local.py`には含めていないため、試す場合は別ターミナルで手動起動してください。

## ディレクトリ構成

```text
app/                         # FastAPIバックエンドの実行コード（唯一の配布パッケージ）
  api/                       # HTTP入出力
  connections/application/   # 接続管理ユースケース
  exchanges/                 # OANDA Practice / Binance Testnetクライアント
  market_data/               # Durable Worker（application/infrastructure/worker）
  notifications/             # Outbox配信・汎用SMTPアダプタ・Notification Worker（スケルトン）
  security/                  # OIDCログイン・セッション・RBAC
  services/                  # 未分割のアプリケーションサービス
  trading/application/       # 注文実行・リスク判定・trading halt・backtest replay
frontend/                    # Reactフロントエンド
tests/                       # 自動テスト
```

現在構造、目標構造、依存方向、段階的な移行方針は
`docs/architecture/current-and-target.md` を参照してください。

## CI/CD

Pull Requestと`main`へのPushで、`.github/workflows/ci.yml`が次の4ジョブを実行します。

- **backend**: 依存関係の既知脆弱性スキャン（`pip-audit`）、Ruff lint/format、mypy（既定 + Windows用分岐）、pytest（DB不要分）
- **worker-storage**: 使い捨てPostgreSQL上でmigration往復試験とWorker/Outbox関連の統合試験
- **secret-scan**: gitleaksによるsecretの混入検出
- **frontend**: ESLint、Vitest、本番ビルド

DependabotがGitHub ActionsとPython依存関係の更新を週次で確認します。

`v1.0.0`のようなタグをPushすると、`release.yml`がPythonパッケージのビルド、
SPDX形式SBOM生成（`anchore/sbom-action`）、GitHub Release作成（パッケージ + SBOM添付）を行います。

```powershell
git tag v0.1.0
git push origin v0.1.0
```

取引所APIキーなどの秘密情報はコミットせず、ローカルでは`.env`、CI/CDではGitHub ActionsのRepository Secretsを使用してください。
