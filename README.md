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

開発用Owner認証とローカル暗号化Secret Storeには、それぞれ別のランダム値を設定します。次のコマンドで値を生成し、表示された値を`.env`の`DEV_OWNER_TOKEN`と`SECRET_ENCRYPTION_KEY`へ設定してください。値はコミット、チャット送信、スクリーンショット共有をしないでください。

```powershell
.\.venv313\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv313\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```dotenv
DEV_OWNER_TOKEN=1つ目のコマンドで生成した値
SECRET_ENCRYPTION_KEY=2つ目のコマンドで生成した値
SECRET_STORE_PATH=.secrets
```

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
バックエンドとフロントエンドを1つのウィンドウで起動し、準備ができたらブラウザーを開きます。

- 起動ウィンドウで **R**: 両方を停止して再起動（Enter不要）。コード変更後も利用できます。
- **Q** または **Ctrl+C**: この起動操作で開始した両方のサーバーを停止して終了。
- ウィンドウの×や強制終了ではなく、Qで終了してください。
- `start-local.bat --check`: 環境・ポートだけ確認し、起動しません（DB接続は確認しません）。
- `start-local.bat --no-browser`: ブラウザーを自動で開かず起動します。

PostgreSQLはあらかじめ起動してください。依存関係のインストール、DBマイグレーション、
`.env`や資格情報の書き換えは自動では行いません。実行可能なプロジェクト内のPython 3.13環境を
`.venv` → `.venv313` の順で選択します。壊れた仮想環境はREADMEの手順で再作成してください。

8000/5173番ポートが使用中なら起動を中止します。以前の手動起動サーバーは、そのターミナルで
停止してからbatを起動してください。他のプロセスを勝手に終了したり、別ポートへ変更したりしません。
両サーバーはローカルPCだけに公開します。バックエンドの自動リロードは使わず、Rで再起動します。
起動すると既存の自動取得設定に従ってデータ取得も再開します。再起動中の手動過去取得は中断され得ます
（再起動からの自動復旧は次のDurable Worker工程）。

### 初期API

- `GET /api/v1/workspaces` — workspace一覧
- `POST /api/v1/workspaces` — workspace作成
- `GET /api/v1/workspaces/{workspace_id}` — workspace詳細
- `GET /api/v1/workspaces/{workspace_id}/connections` — 接続一覧（秘密参照は返さない）
- `POST /api/v1/workspaces/{workspace_id}/connections` — 暗号化した認証情報で接続登録
- `POST /api/v1/workspaces/{workspace_id}/connections/{connection_id}/disable` — 接続無効化
- `POST /api/v1/workspaces/{workspace_id}/connections/{connection_id}/verify` — OANDA practice / Binance Spot Testnet 資格情報検証・口座同期
- `PUT /api/v1/workspaces/{workspace_id}/connections/{connection_id}/credentials` — 暗号化資格情報を置換して即時再検証
- `GET /api/v1/exchanges` — 対応取引所一覧
- `GET /api/v1/markets` — 対応市場一覧

Workspaceと接続APIでは`X-Owner-Token`ヘッダーが必要です。これはローカル開発専用の認証であり、第三者配布前にOIDC認証へ置き換えます。取引所認証情報は`.secrets/`へFernet暗号化して保存し、DBには`local-encrypted://...`形式の参照だけを保存します。

OANDA検証は公式practice APIの口座一覧、口座summary、USD/JPY instrumentを読取専用で取得します。口座IDは暗号化・ハッシュ・マスクして保存し、画面とAPIにはマスク値だけを返します。外部注文endpointは呼び出しません。

### 接続トラブルの確認順

1. `http://localhost:8000/api/v1/health`が開かなければFastAPIを起動する
2. APIは開くが`health/db`が503なら`.env`の`DATABASE_URL`を確認する
3. PostgreSQL側で`general_system_db`、接続ユーザー、パスワード、5432番ポートを確認する
4. 既にDDL適用済みなら、接続成功後に`python -m alembic stamp head`を実行する

## ローカルでの確認

```powershell
ruff check .
ruff format --check .
pytest
```

## ディレクトリ構成

```text
app/                         # FastAPIバックエンドの実行コード
  api/                       # HTTP入出力
  connections/application/   # 接続管理ユースケース
  exchanges/                 # OANDA Practice / Binance Testnetクライアント
  services/                  # 未分割のアプリケーションサービス
frontend/                    # Reactフロントエンド
src/ai_system_trading/       # 配布パッケージ用シェル（統合予定）
tests/                       # 自動テスト
```

現在構造、目標構造、依存方向、段階的な移行方針は
`docs/architecture/current-and-target.md` を参照してください。

## CI/CD

- Pull Requestと`main`へのPushで、Ruffとpytestを実行します。
- DependabotがGitHub ActionsとPython依存関係の更新を週次で確認します。
- `v1.0.0`のようなタグをPushすると、PythonパッケージをビルドしてGitHub Releaseを作成します。

```powershell
git tag v0.1.0
git push origin v0.1.0
```

取引所APIキーなどの秘密情報はコミットせず、ローカルでは`.env`、CI/CDではGitHub ActionsのRepository Secretsを使用してください。
