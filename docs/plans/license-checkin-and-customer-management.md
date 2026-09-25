# ライセンスcheck-in・顧客管理サービス 詳細実装計画

- 根拠: `docs/decisions/0006-license-checkin-and-customer-management-service.md`(ADR 0006)
- 前提: `docs/plans/horizon5-implementation-plan.md` Unit 6(オフライン検証ライセンスキー機構)
- 状態: ドラフト(未着手)

## 0. スコープと分離

ADR 0006の決定どおり、実装は2つのリポジトリにまたがる。

| 部分 | 場所 | 内容 |
|---|---|---|
| **A. check-in送信** | 本リポジトリ(AI_System_Trading) | 顧客環境から新サービスへcheck-inを送る側。小さい追加のみ |
| **B. ライセンス・顧客管理サービス** | **新しい別リポジトリ(名称・配置は未確定、本計画末尾で確認)** | check-in受信、購入者/ライセンスDB、発行API、復旧トークンAPI |

Bは新規プロジェクトであり、本計画はBの設計を含むが、**実際のリポジトリ作成・デプロイ先の選定は
利用者の確認を得てから行う**(GitHubリポジトリ新設、ホスティング費用が発生するインフラ選定は
勝手に実行しない)。

## 1. A: check-in送信(本リポジトリの変更)

### 1.1 目的

顧客環境の`license_id`・インストール識別子・エンドユーザースナップショットを、任意・fail-open
(失敗しても起動やAPI応答を止めない)で新サービスへ送信する。

### 1.2 新規作成ファイル

**`app/licensing/installation_id.py`**

初回起動時に一度だけ生成し、ローカルファイルへ永続化する識別子。同じライセンスファイルが
複数の別インストールにコピーされた場合、`license_id`は同じでも`installation_id`は別になる
ため、これが「1本のライセンスが何箇所で動いているか」の実体的なシグナルになる。

```python
"""Per-installation identifier, generated once and persisted locally
(separate from license_id, which is shared across every copy of the same
license file -- installation_id is what actually distinguishes "one license,
five installs" from "one license, one install" when check-in reports arrive
at the licensing service. Not a secret; safe to log, but not meant to be
guessable/predictable, hence uuid4."""

import uuid
from pathlib import Path


def load_or_create_installation_id(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    installation_id = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(installation_id, encoding="utf-8")
    return installation_id
```

**`app/licensing/checkin.py`**

```python
"""Fail-open check-in to the licensing service (ADR 0006). Never raises past
this module's own boundary -- a broken/unreachable licensing service must
never affect this application's own availability. This is a deliberate,
narrow exception to this project's usual "don't swallow errors" rule
(AGENTS.md §6): the whole point of ADR 0006's fail-open design is that
check-in failures are invisible to the running application."""

from dataclasses import dataclass
from importlib.metadata import version

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workspace import AppUser

logger = structlog.get_logger(__name__)
_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class EndUserSnapshot:
    external_user_id: str
    email: str
    display_name: str
    status: str


def _end_user_snapshot(db: Session) -> list[EndUserSnapshot]:
    users = db.scalars(select(AppUser)).all()
    return [
        EndUserSnapshot(
            external_user_id=str(user.id),
            email=user.email,
            display_name=user.display_name,
            status=user.status,
        )
        for user in users
    ]


async def send_checkin(
    db: Session,
    *,
    checkin_url: str,
    license_document: dict[str, object],
    installation_id: str,
) -> None:
    payload = {
        "installation_id": installation_id,
        "app_version": version("ai-system-trading"),
        "license_document": license_document,  # full signed doc; the service
        # re-verifies the signature itself rather than trusting a bare
        # license_id, so a forged check-in can't claim an unissued license.
        "end_users": [snapshot.__dict__ for snapshot in _end_user_snapshot(db)],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(checkin_url, json=payload)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        # Fail-open: log and move on. See module docstring.
        logger.warning("license_checkin_failed", error=str(exc))
```

`importlib.metadata.version("ai-system-trading")`は`pyproject.toml`の`[project] version`を
実行時に読む標準ライブラリAPI([Python公式: importlib.metadata](https://docs.python.org/3/library/importlib.metadata.html))で、新規依存は不要。

### 1.3 起動時の配線(`app/main.py`)

FastAPIの`lifespan`内で、`settings.app_env != "local"`かつ`settings.license_checkin_enabled`
かつ`settings.license_checkin_url`が設定されている場合のみ、バックグラウンドタスクとして
起動直後に1回、以後`license_checkin_interval_seconds`間隔でcheckinを送るループを開始する。
市場データWorkerのような別プロセスにはしない(この処理はAPIプロセスの寿命に従えばよく、
独立ライフサイクルを持たせる理由が無いため)。

タスク内で例外が発生してもAPI全体をクラッシュさせないよう、ループ本体を
`try/except Exception`(この1箇所に限り、`checkin.py`自体は`httpx.HTTPError`のみを
握りつぶす設計だが、呼び出し側のループはローカル側の予期しないバグ(DBセッション取得失敗等)
からも隔離する)で包む。

### 1.4 設定追加(`app/core/config.py`)

```python
license_checkin_url: str | None = None
license_checkin_enabled: bool = True
license_checkin_interval_seconds: float = 24 * 60 * 60  # 24h
license_installation_id_path: Path = Path(".license_installation_id")
```

`.env.example`へ追記し、`.gitignore`へ`.license_installation_id`を追加する。

### 1.5 テスト方針

- `test_installation_id.py`: 初回生成・2回目以降の読み込み一貫性
- `test_license_checkin.py`: ペイロード構築(MagicMock Sessionで`AppUser`行を用意)、
  `httpx.HTTPError`発生時に例外を外へ伝播させないこと(fail-open)、成功時に正しいURL/
  ペイロードでPOSTすること(`respx`でモック、既存の`test_oidc_client.py`と同じ手法)

### 1.6 想定リスク

低。既存の`require_valid_license`(Unit 6、オフライン検証)には一切手を入れない。
check-in機構が全体として不調でも、ライセンス検証自体・API応答には影響しない設計。

---

## 2. B: ライセンス・顧客管理サービス(新リポジトリ)

以降は新サービスの設計。**実装着手前に、リポジトリ名・GitHub上の配置・ホスティング先を
利用者に確認する**(本計画末尾「2.7 未確定事項」参照)。

### 2.1 技術スタック(提案)

本体との一貫性を優先し、同じ技術選定に揃える: FastAPI + SQLAlchemy 2.0 + Alembic +
PostgreSQL + Pydantic Settings。新しい技術を導入する理由が無い(AGENTS.md §5)。

### 2.2 データモデル

```text
purchaser
  id, company_name, contact_email(unique), created_at

license
  id(=license_idとしてapp側へ配布), purchaser_id(FK), features(jsonb),
  issued_at, expires_at(nullable), status(active/revoked), signing_key_version

purchase_event
  id, license_id(FK), event_type(purchase/renewal/refund/manual_issue),
  note, occurred_at

checkin
  id, license_id(FK), installation_id, checked_in_at, app_version,
  source_ip, end_user_count

end_user
  id, license_id(FK), external_user_id, email, display_name, status,
  first_seen_at, last_seen_at
  UNIQUE(license_id, external_user_id) -- checkinのたびにupsert、
  checkinごとに行を増やさない(無制限増加を避ける)

recovery_request
  id, purchaser_id(FK), requested_email, requested_at,
  verification_token_hash, verified_at(nullable),
  issued_license_id(nullable, FK), expires_at, used_at(nullable)
```

`verification_token_hash`は生トークンではなくハッシュを保存する(本体の
`app/security/session.py`のセッションJWT設計とは別方式だが、「生の秘密値をDBに保存しない」
という原則は共通)。

### 2.3 API

| メソッド・パス | 認証 | 内容 |
|---|---|---|
| `POST /api/v1/checkin` | なし(license_document自体の署名検証で真正性を担保) | check-in受信、`checkin`/`end_user`行をupsert |
| `POST /api/v1/licenses` | 運営者内部認証(詳細別途) | 新規ライセンス発行(Ed25519署名、`scripts/issue_license.py`のロジックを再利用) |
| `POST /api/v1/recovery/request` | なし(メール到達性で担保) | 購入者がメールアドレスを送信、該当purchaserが存在してもしなくても同じレスポンスを返す(メールアドレス在不在の推測を防ぐ、既知パターン) |
| `GET /api/v1/recovery/verify` | トークン(URLクエリ) | トークン検証、有効なら再署名したlicense.jsonを発行しメール送付 |

### 2.4 復旧フロー詳細

1. 購入者が`POST /recovery/request`に登録メールアドレスを送信。
2. サービスは該当purchaserの有無に関わらず同じ「メールを確認してください」レスポンスを返す。
3. 該当purchaserが存在する場合のみ、ランダムトークンを生成しハッシュをDBへ保存、有効期限
   (例: 30分)付きの検証URLをメール送信。
4. 購入者がリンクを踏むと`GET /recovery/verify?token=...`が呼ばれ、トークンハッシュを照合。
5. 有効なら、そのpurchaserの最新licenseと同条件(features、残存期間 or 元のexpires_at)で
   再署名した新しいlicense.jsonを生成し、メールに添付して送付。`recovery_request.used_at`を
   記録し、同じトークンの再利用を防ぐ。
6. レート制限: 同一メールアドレスへは1時間に3回まで、同一IPへは1時間に10回まで
   (具体的な閾値は実装時に運用しながら調整可能な設定値とする)。

### 2.5 鍵管理

Ed25519秘密鍵は、Unit 6の当初想定(運営者のローカル環境のみ、CLIで都度使用)から、
本サービスが常時保持する形へ変わる(ADR 0006「残存リスク」で明記済み)。サービス側は
環境変数または稼働先のシークレット管理機構(例: Fly.ioのsecrets、AWS Secrets Manager等、
ホスティング先確定後に選定)から読み込む。**リポジトリにはコミットしない**(本体の
`.secrets/`運用と同じ原則)。

`scripts/issue_license.py`(本体リポジトリ)は、本サービスが無い状態でも手動発行できる
フォールバックとして残す。

### 2.6 テスト方針

新サービス側の詳細は着手時に別途定める。最低限: 署名検証ロジックの単体テスト、
check-in受信のupsert冪等性(同じinstallation_idから2回送っても`end_user`行が重複しない)、
復旧トークンのワンタイム性(使用後に再利用できない)、メールアドレス在不在で応答が
変わらないこと。

### 2.7 未確定事項(着手前に利用者確認が必要)

- リポジトリ名・GitHub上の配置(個人アカウント/組織)
- ホスティング先(小規模PaaS、VPS等)とその費用
- メール送信手段(SMTP、本体の`app/notifications/adapters/smtp.py`を流用できる可能性あり)
- 運営者内部認証(`POST /api/v1/licenses`を呼べるのは誰か)の方式
- プライバシーポリシー・利用規約の文言(ADR 0006の残存リスク、法務確認が別途必要)

## 3. 実装順序

1. 本リポジトリ: `installation_id`生成・永続化(1.2前半、テスト込み)
2. 本リポジトリ: check-inペイロード構築+fail-open送信(1.2後半、1.3、1.4、テスト込み)
   — ここまでは新サービスが存在しなくても実装・テストできる(送信先URLをモックで検証)
3. 利用者確認: 2.7の未確定事項を決定
4. 新サービス: リポジトリ作成、データモデル+migration
5. 新サービス: check-in受信エンドポイント
6. 新サービス: ライセンス発行エンドポイント(`scripts/issue_license.py`のEd25519ロジックを移植)
7. 新サービス: 復旧リクエスト/検証エンドポイント+メール送信
8. 本体`docs/plans/horizon5-implementation-plan.md` Unit 6・`docs/knowledge/self-hosted-deployment.md`
   (Unit 10)へ、check-inの存在・無効化方法・プライバシー上の扱いを追記
