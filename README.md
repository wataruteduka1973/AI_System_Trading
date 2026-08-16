# AI System Trading

AIモデルとテクニカル分析指標を利用し、複数の取引所にまたがる取引を管理するためのプロジェクトです。

> [!WARNING]
> 現在は開発初期段階です。実際の資金を使った取引には使用しないでください。

## 開発環境

- Python 3.12以上
- Git

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## ローカルでの確認

```powershell
ruff check .
ruff format --check .
pytest
```

## ディレクトリ構成

```text
src/ai_system_trading/
  core/        # 共通設定・ドメインロジック
  exchanges/   # 取引所ごとの接続処理
  strategies/  # 売買戦略・AIモデル
tests/         # 自動テスト
```

## CI/CD

- Pull Requestと`main`へのPushで、Ruffとpytestを実行します。
- DependabotがGitHub ActionsとPython依存関係の更新を週次で確認します。
- `v1.0.0`のようなタグをPushすると、PythonパッケージをビルドしてGitHub Releaseを作成します。

```powershell
git tag v0.1.0
git push origin v0.1.0
```

取引所APIキーなどの秘密情報はコミットせず、ローカルでは`.env`、CI/CDではGitHub ActionsのRepository Secretsを使用してください。
