# Horizon 5 実装計画: OIDC認証・RBAC・配布運用の完成

本ドキュメントは、本セッションの文脈を持たない別のAIコーディングエージェントが、追加調査や質問なしにそのまま実装へ着手できることを目的とした、単体で完結する実装計画である。人間のレビューは想定しない。

## 0. この計画の位置づけと参照元

### 0.1 要件定義の情報源

このリポジトリには `CONTEXT.md` に相当する独立ファイルも、Horizon 5専用のGitHub Issueも存在しない（2026-09-20時点、`gh issue list --state all` は0件）。実際に存在し、本計画が要件定義として採用したドキュメントは次の3点のみである。

1. `docs/decisions/0005-horizon5-self-hosted-distribution.md` (ADR 0005) — 配布モデルの決定（セルフホスト型ソフトウェアライセンス販売への一本化）と、その法的背景。
2. `docs/plans/horizon5-distribution-and-auth.md` — ADR 0005を受けたドラフト実装計画。本ドキュメントはこれを置き換えるものではなく、そこで洗い出された論点（`user_membership`は既存、`system_worker`は別テーブル、Secret抽象化が未整備、等）を引き継ぎ、コードレベルまで具体化したものである。
3. `docs/architecture-alignment-and-long-term-roadmap.md` の「Horizon 5」節 — 開始条件・完了条件・実装/整備項目のロードマップ原文。

本計画はこの3点と、実装時点の実コード（後述の「1. 実装前に確認した既存コードの事実」）を突き合わせて作成した。

### 0.2 着手条件（Stop Condition）

`docs/plans/horizon5-distribution-and-auth.md` は「本格着手はHorizon4-lite（Backtest）完了後に、利用者へ改めて承認を得てから行う。本ドキュメント単体を実装着手の許可として扱わない」と明記している。本計画も同じ制約を引き継ぐ。**実装エージェントは、着手前に次の2点を利用者に確認すること。**

1. Horizon4-lite（`docs/plans/horizon4-lite-backtest.md`）が完了しているか（2026-09-20時点で本計画作成者が確認した範囲では、直近コミット `33de62d`（Unit6: walk-forward分割）が最後のHorizon4-lite関連コミットであり、同計画書内に明示的な完了宣言（`状態: [x] 実装済み`のような記載）は見当たらなかった）。
2. Horizon5本体（本計画のUnit 1以降）に着手してよいか。

この確認を経るまで、Unit 1以降のコード変更を開始しない。

### 0.3 スコープ外(本計画では扱わない)

- マネージドホスティング（ADR 0005により当面提供しない）
- 有償サポート契約・SLA
- Gmail API等、汎用SMTP以外の通知アダプタの実装（インターフェースは用意するが具体アダプタ追加は別タスク）
- ライセンス販売形態（買い切り/サブスク）の最終決定（本計画のライセンス機構はどちらでも使える設計とする）
- 自己完結型内蔵IdP（外部IdP接続のみを実装し、内蔵IdPは将来の別タスクとする。理由はUnit 3参照）
- GCP Secret Manager / HashiCorp Vaultバックエンドの実装（Protocolと最初の実装例としてAWS Secrets Manager/KMSのみ実装し、他バックエンドは同じProtocolに従う後続タスクとする）
- E2E（Playwright）試験の追加（`docs/plans/ci-quality-gate-hardening.md` で既に「先送り」と決定済み。本計画もそれに従う）

---

## 1. 実装前に確認した既存コードの事実

計画作成にあたり実コードを調査した結果、要件定義ドキュメント（特にドラフト計画）作成時点では分かっていなかった、または実装方法に直結する事実が判明した。実装エージェントはこれらを前提としてよい（再調査不要）。

1. **`user_membership` テーブルは既存**。`database/postgresql_schema_v0.1.sql` 46-52行目に定義済みで、`alembic/versions/20260816_0001_initial_schema.py` がこのSQLファイルをそのまま実行する形で初回migrationとして適用済み。カラムは `workspace_id, user_id, role (CHECK IN owner/operator/viewer), created_at`、主キーは `(workspace_id, user_id)`。**ORMモデルは未実装**（`app/models/workspace.py` には `Workspace` と `AppUser` のみ）。新規migrationは不要、ORM追加のみでよい。
2. **`app_user.status` は `invited` / `active` / `disabled` のCHECK制約を持つ**（同SQL 33-44行目）。`oidc_subject` カラムも既存（unique）。
3. **`notification` テーブルと `outbox_event` テーブルも既存**（同SQL 603-616行目、821-834行目）。`notification` は `event_id` で `system_event` (同552行目、`app/models/audit.py` に `SystemEvent` として実装済み) を参照する。**これらもORMモデルは未実装**。新規migrationは不要、ORM追加のみでよい。これはドラフト計画が「新規作成対象」として想定していた範囲より作業量が少ないことを意味する。
4. **`service_account`（またはAPIキー）に相当するテーブルは存在しない**。`system_worker` ロールは `user_membership` のCHECK制約に含まれておらず（owner/operator/viewerのみ）、人間のワークスペースメンバーシップとは性質が異なるサービスアイデンティティである。**新規migrationが必要**（Unit 5）。
5. **ライセンスキーに相当するテーブル・機構は存在しない**。完全にオフライン検証（署名済みファイル）で実装し、DBテーブルは不要（Unit 6）。
6. **現在の認証は `app/security/auth.py` の `require_owner` のみ**。固定トークン（環境変数 `DEV_OWNER_TOKEN`）を `x-owner-token` ヘッダと `secrets.compare_digest` で比較するだけで、ユーザーもワークスペースメンバーシップも一切見ない。認証済みAPIルートは6ファイル・22エンドポイント（詳細はUnit 4参照）。
7. **`app/services/secrets.py` の `LocalEncryptedSecretStore` は具象クラスのみでインターフェースが無い**。Fernet（`cryptography.fernet`）で暗号化。`put/get/delete/encrypt_text/decrypt_text` の5メソッド。
8. **`.github/workflows/release.yml` はタグpush起点でPythonパッケージをビルドしGitHub Releasesへ公開済み**。SBOM生成・依存関係脆弱性スキャンは未追加。`.github/workflows/ci.yml` にはsecret scan（gitleaks、Docker実行）が既にPR単位で存在する。依存関係脆弱性スキャン（pip-audit相当）はCI・releaseどちらにも存在しない。
9. **短命JWT署名の先例が既にある**: `app/market_data/infrastructure/stream_tickets.py` が `PyJWT`（HS256、`app.core.config.settings` 由来の秘密鍵）で短命ワンタイムチケットを発行・検証している。本計画のセッションJWT（Unit 3）・サービスアカウントAPIキー（Unit 5、ただしJWTではなくハッシュ照合）はこのモジュールの設計を踏襲する。
10. **インストール済みライブラリのバージョン**（`pyproject.toml` / `pip show`、2026-09-20時点）:
    - `fastapi>=0.139,<0.142`（実体0.141.1）
    - `SQLAlchemy>=2.0.51,<2.1`（実体2.0.52）
    - `alembic>=1.16,<2`（実体1.19.1）
    - `cryptography>=46,<47`（実体46.0.7）
    - `PyJWT[crypto]>=2.10,<3`（実体2.14.0）
    - `pydantic-settings>=2.14,<3`
    - `psycopg[binary,pool]>=3.3.4,<4`
    - `httpx>=0.28,<1`（現状は `dev` extraのみ、実体0.28.1。**本計画でcore依存へ昇格**、理由はUnit 3参照）
    - Python `>=3.13,<3.14`
    - lockファイルは存在しない（`pip install -e ".[dev]"` で都度解決）。
11. **Application層の実装パターンは2系統ある**。`app/connections/application/` はクラスベースのUse Case（例: `VerifyConnectionUseCase`）、`app/trading/application/trading_halt.py` は関数ベースのモジュール（`dataclass(frozen=True)` のスコープ値オブジェクト + トップレベル関数）。本計画では、状態を持たない純粋な検証・変換ロジック（OIDC検証、ライセンス検証、RBAC判定）は関数ベース、DBセッションと外部クライアントを組み合わせて手順を実行するもの（OIDCコールバック処理、通知配信）はクラスベースUse Caseとする。
12. **全APIルートは `workspace_id: UUID` をパスパラメータに取る**（`/client-logs` を除く）。FastAPIの依存関数もエンドポイントと同じリクエストスコープでパスパラメータ名を共有できるため、`workspace_id` を引数に取る依存関数を書けば、そのエンドポイントの `workspace_id` を自動的に受け取れる（[FastAPI公式: Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/) の依存関数チュートリアルに準拠する標準パターン）。

---

## 2. 全体設計方針

### 2.1 認証・セッション方式

- 外部OIDC IdP（Authorization Code + PKCE、[RFC 7636](https://www.rfc-editor.org/rfc/rfc7636)）のみを実装する。自己完結型内蔵IdPは実装しない。理由: 内蔵IdPはユーザー登録・パスワードリセット・MFA等を自前実装する必要があり、スコープが本Horizonの他項目と同等以上に大きい。ADR 0005の配布対象（クローズドβ、限定的な顧客）であれば、顧客が既に持つ外部IdP（Auth0、Okta、Microsoft Entra ID等、OIDC discoveryに対応するもの）への接続で運用可能と判断した。
- IDトークンの署名検証は `PyJWT` の `PyJWKClient`（JWKSエンドポイントから鍵を取得、RS256）を使う。新規ライブラリ（Authlib等）は追加しない。理由: `PyJWT` は既に依存関係にあり（`stream_tickets.py` で使用実績あり）、OIDC discoveryとPKCE自体は標準ライブラリ（`hashlib`, `secrets`, `base64`）+ `httpx` で十分に書ける薄い処理であるため（AGENTS.md §3「既存の仕組みで実現可能なら、新しい仕組みを追加しない」）。
- ログイン後のアプリセッションは、`stream_tickets.py` と同じ方式（PyJWT HS256、プロセス固有の署名鍵）でセッションJWTを発行し、**HttpOnly + Secure + SameSite=Strict Cookie**（Cookie名 `session`）として返す（ブラウザSPAからの資格情報保存にlocalStorageを使わないことで、XSS経由のトークン窃取を防ぐ）。CSRF対策は `SameSite=Strict` に一本化し、別途CSRFトークンは実装しない（同一オリジンでフロントエンドとAPIを配信するセルフホスト構成が前提のため。クロスオリジン埋め込みが将来要件になった場合は別途見直す）。
- **PKCE用の一時Cookie（`oidc_state`、Unit 3.2節）だけは`SameSite=Lax`にする。** `SameSite=Strict`のCookieは、外部IdPからのリダイレクトで自オリジンへ戻ってくる（＝他サイトが起点のトップレベルナビゲーション）際には送信されないため、`/auth/callback`が`oidc_state`を読めず認可コードフロー自体が失敗する。GETベースのトップレベルナビゲーションで送信される`SameSite=Lax`であれば、この往復に対応できる（[MDN: SameSite cookies](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Set-Cookie/SameSite)）。認証確立後の通常API呼び出しは同一オリジンのfetchのみのため、`session` Cookie自体は`SameSite=Strict`のままでCSRF対策として機能する。
- セッションJWTはstatelessなため、有効期限前のサーバー側強制失効はできない（ログアウトはCookie削除のみ）。この制約は想定リスクとして明記する（Unit 3参照）。

### 2.2 RBAC方式

- ロールの強度順は `viewer(1) < operator(2) < owner(3)`。`app/trading/application/trading_halt.py` の `_LEVEL_SEVERITY` と同じ「辞書 + 数値比較」パターンを踏襲する。
- 各APIルートは「そのワークスペースで最低限必要なロール」を1つ持つ。実際のロール判定は `user_membership.role` を引く共通の依存関数で行う（Unit 4）。
- ワークスペース作成（`POST /workspaces`）だけは例外で、対象ワークスペースがまだ存在しないため「認証済みユーザーであること」のみを要求し、作成と同時に作成者を `owner` として `user_membership` に登録する。
- `GET /workspaces`（一覧）は、**認証済みユーザーが所属するワークスペースのみを返すよう動作が変わる**（現状は無条件に全件返す）。これは複数ユーザー・RBAC導入に伴う意図的なAPI契約変更であり、ロードマップの完了条件「Workspaceを跨いだ閲覧が不可能」を満たすために必須。実装エージェントは既存のfrontend（`frontend/src/features/` 配下）がこの変更を前提としているかを確認し、影響があれば併せて修正すること（本計画はbackendのAPI契約定義までを範囲とし、frontendの詳細な画面改修はスコープ外とするが、ビルド・型エラーが出る場合は追随修正してよい）。
- `GET /exchanges`, `GET /markets`（`app/api/routes/connections.py`）は現状無認証だが、ワークスペースに紐づかないグローバルなカタログ参照のため「認証済みユーザーであること」のみを要求する（ロール不問）。
- 具体的なエンドポイント別の必要ロールはUnit 4の表を正とする。

### 2.3 品質管理の実行手順

AGENTS.mdに「品質管理の実行手順」という名前の独立した節は存在しない（確認済み、2026-09-20時点）。AGENTS.md §8（Static Verification）が要求する項目と、実際に `.github/workflows/ci.yml` が実行する内容を統合し、本計画における実行手順を次のとおり定義する。**全Unit共通で、実装完了時に必ずこれを実行する。**

```bash
# 1. Python依存関係を開発用込みでインストール（初回のみ、以後は差分のみ）
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

# 2. Lint
ruff check .

# 3. フォーマット確認
ruff format --check .

# 4. 型検査（Linux既定 + Windows launcher分岐）
python -m mypy
python -m mypy --platform win32

# 5. テスト（DBを使わないもの）
python -m pytest

# 6. PostgreSQLを使うテスト（Unit 1, 5, 8でテーブルに触れる場合は必須）
#    ci.yml の worker-storage ジョブと同じ使い捨てPostgresを使う。
#    ローカルでは docker run --rm -e POSTGRES_USER=worker_test \
#      -e POSTGRES_PASSWORD=worker_test -e POSTGRES_DB=worker_test_ci \
#      -p 5432:5432 postgres:16 のようなコンテナを一時的に立てて代用してよい。
WORKER_TEST_DATABASE_URL=postgresql+psycopg://worker_test:worker_test@127.0.0.1:5432/worker_test_ci \
  python -m pytest tests/test_migrations_roundtrip.py tests/test_worker_leases_postgres.py \
  tests/test_worker_lease_contracts.py tests/test_worker_pages.py tests/test_worker_runner_postgres.py

# 7. secret scan（差分に資格情報が混入していないか）
docker run --rm -v "$PWD:/repo" zricethezav/gitleaks:latest \
  detect --no-git --source="/repo" --config="/repo/.gitleaks.toml" --redact --exit-code 1 -v

# 8. frontendに変更がある場合のみ
cd frontend
npm ci
npm run lint
npm run test
npm run build
cd ..

# 9. Pythonパッケージビルド（Unit 9で追加するSBOM/脆弱性スキャンの対象確認も兼ねる）
python -m build
```

`docs/quality/definition-of-done.md` のチェックリストも完了報告前に確認する。実行できない項目（例: OANDA/Binance実接続を要する確認）は `NOT VERIFIED` として明示し、理由を書く。

### 2.4 テスト方針（共通）

- 配置場所: `tests/` 直下フラット構成（既存の慣習どおり、サブディレクトリを作らない）。
- 命名: `test_<対象>.py`。既存ファイルと衝突しない名前を選ぶこと（Unit毎に案を示す）。
- 観点: 各Unitの「テスト方針」に記載する個別観点に加え、共通で次を必ず確認する。
  - 正常系（許可されるロール/資格情報で成功する）
  - 認可の境界（不足ロール・他ワークスペースからのアクセスが403/404で拒否される）
  - 既存の `require_owner` を前提にしていたテスト（`tests/test_*_api.py` 群の `override_database` 関数）は、認可方式の変更に追随して更新し、**全て成功すること**を regression として確認する。
- 秘密情報（トークン、秘密鍵、APIキー）をテストのアサーションやログ出力に平文で残さない（`app/core/logging.py` の redaction方針に合わせる）。

---

## 3. 実装順序と各Unit

実装単位は既存ドラフトの10 Unitを踏襲しつつ、コードレベルまで具体化した。**Unit番号順に実装し、各Unit完了時に第2.3節の品質管理手順を実行してから次のUnitへ進む。**

### Unit 1: `UserMembership` ORM + `Workspace`/`AppUser` との relationship 配線

#### 目的
既存DBスキーマの `user_membership` テーブルに対応するORMモデルを追加し、RBAC実装（Unit 4）が使える状態にする。migrationは不要（テーブルは既存）。

#### 変更対象ファイル
- `app/models/workspace.py`: `UserMembership` クラスを追加。`Workspace.memberships` / `AppUser.memberships` relationshipを追加。

#### 実装詳細

```python
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship


class UserMembership(Base):
    __tablename__ = "user_membership"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'operator', 'viewer')", name="ck_user_membership_role"),
        {"schema": SCHEMA},
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.app_user.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped["Workspace"] = relationship(back_populates="memberships")
    user: Mapped["AppUser"] = relationship(back_populates="memberships")
```

`Workspace` に `memberships: Mapped[list["UserMembership"]] = relationship(back_populates="workspace")` を、`AppUser` に同様の `memberships` を追加する。列の型・CHECK制約はDBの `database/postgresql_schema_v0.1.sql` 46-52行目と完全に一致させること（新しいCHECK制約をAlembicで追加しない。DB側に既にある制約をORM側に反映するだけ）。

既存の `workspace.py` と同じく `from uuid import UUID` を使い、`Mapped[UUID]` で型注釈すればSQLAlchemyがPostgreSQLの `uuid` 型へ自動マッピングする（`sqlalchemy.dialects.postgresql.UUID` を明示importする必要はない）。

#### 新規作成ファイル
なし。

#### テスト方針
- `tests/test_user_membership_model.py`（新規）: `Workspace` / `AppUser` / `UserMembership` を作成し、relationship経由で辿れることを確認する単体テスト（MagicMockではなく、Unit 5と同様に専用PostgreSQLを使う統合テストとして書く。`tests/test_worker_leases_postgres.py` の `WORKER_TEST_DATABASE_URL` 参照パターンを流用する）。
- CHECK制約違反（`role='system_worker'` 等の不正値）を挿入しようとするとDBがエラーを返すことを確認するテストを1本含める。

#### 想定リスク
低。既存テーブルへのORM追加のみで、他コードへの影響はない。

---

### Unit 2: `SecretStore` インターフェース抽出

#### 目的
`LocalEncryptedSecretStore` が満たすべきインターフェースを `Protocol` として切り出し、Unit 7（AWS Secrets Manager/KMSバックエンド）が同じ型として扱えるようにする。既存呼び出し元の挙動は一切変更しない。

#### 変更対象ファイル
- `app/services/secrets.py`: `SecretStore` Protocolを追加。`LocalEncryptedSecretStore` 自体のコードは変更不要（構造的部分型のため、Protocolのメソッドシグネチャと一致していれば自動的に適合する）。
- `app/api/routes/connections.py`, `app/api/routes/market_stream_ws.py` ほか `LocalEncryptedSecretStore` を型ヒントに使っている箇所: `Annotated[LocalEncryptedSecretStore, Depends(get_secret_store)]` を `Annotated[SecretStore, Depends(get_secret_store)]` に変更（`SecretStore` をimportし直す）。`get_secret_store()` 自体の戻り値型注釈も `SecretStore` に変更してよい（実行時の戻り値は変わらず `LocalEncryptedSecretStore` インスタンスのまま）。

#### 実装詳細

```python
from typing import Protocol


class SecretStore(Protocol):
    """Structural contract every pluggable secret backend must satisfy
    (`LocalEncryptedSecretStore` and, later, `app/services/secrets_backends/`
    implementations). See docs/decisions/0005 and
    docs/plans/horizon5-implementation-plan.md Unit 7 for why this
    exists: distribution customers may run their own KMS/Secrets Manager
    instead of the bundled local encrypted store."""

    def put(self, values: dict[str, str]) -> str: ...
    def get(self, secret_ref: str) -> dict[str, str]: ...
    def delete(self, secret_ref: str) -> None: ...
    def encrypt_text(self, value: str) -> str: ...
    def decrypt_text(self, value: str) -> str: ...
```

`Protocol` は `typing.Protocol`（標準ライブラリ、Python 3.8+）。`runtime_checkable` は不要（`isinstance` チェックはしない。型検査時の構造的部分型付けのみ利用する）。

#### 新規作成ファイル
なし。

#### テスト方針
- 新規テストは不要（挙動変更なし）。`python -m mypy` が通ることが検証そのもの。
- `tests/test_secret_store.py` の既存テストがそのまま成功することをregressionとして確認する。

#### 想定リスク
低。型注釈レベルの変更のみ。

---

### Unit 3: OIDCクライアント（Authorization Code + PKCE、外部IdPのみ）+ セッション

#### 目的
外部OIDC IdPへのログインリダイレクト、コールバック処理（PKCE検証、IDトークン検証、`AppUser` 解決）、セッションJWT発行を実装する。

#### 新規作成ファイル

1. **`app/security/oidc.py`** — OIDCクライアント本体（discovery、PKCE、token交換、IDトークン検証）。
2. **`app/security/session.py`** — セッションJWTの発行・検証（`stream_tickets.py` と同じ設計）。
3. **`app/security/rbac.py`** — `require_authenticated_user` 依存関数（Unit 4で `require_workspace_role` も同ファイルに追加）。
4. **`app/api/routes/auth.py`** — `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/me` の4エンドポイント。
5. **`app/schemas/auth.py`** — `AuthenticatedUserRead` 等のレスポンススキーマ。

#### 変更対象ファイル
- `app/core/config.py`: 新規設定を追加（後述）。
- `app/models/workspace.py`: `AppUser` に `status` 値の妥当性チェックは不要（DB CHECK制約が既に存在。ORM側は現状どおり `str` のまま）。
- `app/api/router.py`: `auth.router` を登録する。
- `app/main.py`: 変更なし（`auth.router` は `api_router` 経由でまとめて登録されるため）。
- `.env.example`, `pyproject.toml`: 後述。

#### 実装詳細

##### 3.1 設定追加（`app/core/config.py`）

```python
oidc_issuer: str | None = None
oidc_client_id: str | None = None
oidc_client_secret: SecretStr | None = None
oidc_redirect_uri: str = "http://localhost:8000/api/v1/auth/callback"
oidc_scopes: str = "openid email profile"
session_signing_secret: SecretStr | None = None
session_ttl_seconds: int = 8 * 60 * 60
```

`oidc_issuer` は discovery document のベースURL（例: `https://your-tenant.auth0.com/`）。discoveryエンドポイントは `{issuer}/.well-known/openid-configuration`（[OpenID Connect Discovery 1.0 §4](https://openid.net/specs/openid-connect-discovery-1_0.html#ProviderConfig)）。

##### 3.2 PKCE（`app/security/oidc.py`）

[RFC 7636 §4.1-4.2](https://www.rfc-editor.org/rfc/rfc7636#section-4.1) に従い、`code_verifier` は43〜128文字の `[A-Za-z0-9\-._~]` からなるランダム文字列、`code_challenge` はそのSHA-256ダイジェストをbase64url(パディングなし)エンコードしたもの。

```python
import base64
import hashlib
import secrets


def generate_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) per RFC 7636 section 4.1-4.2."""
    code_verifier = secrets.token_urlsafe(64)  # ~86 chars, well within 43-128
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge
```

`code_verifier` は `/auth/login` から `/auth/callback` までの間、サーバー側で一時保持する必要がある（IdPには渡らない値のため）。本計画では、これも短命署名済みCookie（`oidc_state` Cookie、`session.py` と同じHS256署名、TTL 10分、**`SameSite=Lax`** — 理由は2.1節）に `state`・`code_verifier`・`nonce` をまとめて入れて持ち回す方式とする。サーバー側セッションストア（Redis等）を新設しない理由はAGENTS.md §5「当面採用しないもの」に従う。

##### 3.3 Discovery + トークン交換（`httpx` 使用）

`httpx` は現状 `dev` extraのみの依存だが、本Unitから実行時（アプリ本体）が外部IdPへHTTP通信する必要があるため、`pyproject.toml` の `[project.dependencies]` へ昇格させる（`dev` extraからは重複削除）。

```toml
dependencies = [
  # ...既存の行はそのまま...
  "httpx>=0.28,<1",
]
```

discovery documentの取得と、認可コードのトークン交換は次の形（[httpx公式: Making Requests](https://www.python-httpx.org/quickstart/)、[httpx公式: Async Support](https://www.python-httpx.org/async/) に準拠）:

```python
async def fetch_discovery_document(issuer: str) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
        response.raise_for_status()
        return response.json()


async def exchange_code_for_tokens(
    token_endpoint: str,
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
                "code_verifier": code_verifier,
            },
        )
        response.raise_for_status()
        return response.json()
```

##### 3.4 IDトークン検証（`PyJWT` の `PyJWKClient`）

[PyJWT公式: Retrieve RSA signing keys from a JWKS endpoint](https://pyjwt.readthedocs.io/en/stable/usage.html#retrieve-rsa-signing-keys-from-a-jwks-endpoint) の例に従う（インストール済みPyJWT 2.14.0で動作確認済みのAPI）。

```python
import jwt
from jwt import PyJWKClient


def verify_id_token(
    id_token: str, *, jwks_uri: str, audience: str, issuer: str
) -> dict[str, object]:
    jwks_client = PyJWKClient(jwks_uri)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token,
        signing_key,
        algorithms=["RS256"],
        audience=audience,
        issuer=issuer,
    )
```

`PyJWKClient` は内部でJWKSレスポンスをキャッシュする（`PyJWKClient.__init__` の `cache_keys` 引数、既定 `True` 相当の挙動）ため、毎リクエストでJWKSを再取得しない。プロセス単位のシングルトンとして `functools.lru_cache` で保持する（`get_default_used_ticket_store()` と同じパターン）。

`PyJWKClient.get_signing_key_from_jwt` は内部で `urllib` による同期HTTP呼び出しを行う（本計画の他のIdP通信が `httpx.AsyncClient` を使うのとは異なる）。`async def` のコールバックルートハンドラ内から直接呼ぶとイベントループをブロックするため、`await asyncio.to_thread(verify_id_token, ...)` でラップすること（`app/api/routes/market_stream_ws.py` の `_resolve_credentials_sync` が同じ理由で `asyncio.to_thread` を使っている先例に揃える）。

##### 3.5 `AppUser` 解決

```python
def resolve_or_create_app_user(db: Session, *, oidc_subject: str, email: str, display_name: str) -> AppUser:
    user = db.scalar(select(AppUser).where(AppUser.oidc_subject == oidc_subject))
    if user is not None:
        return user
    user = db.scalar(select(AppUser).where(AppUser.email == email))
    if user is not None:
        user.oidc_subject = oidc_subject  # invited via email, first login links the subject
        user.status = "active"
        return user
    user = AppUser(email=email, display_name=display_name, oidc_subject=oidc_subject, status="active")
    db.add(user)
    db.flush()
    return user
```

`status == 'disabled'` のユーザーはログインを拒否する（`/auth/callback` で403）。`status == 'invited'` は「メールで招待済みだが未ログイン」を表す既存の状態遷移（`docs/plans/horizon5-distribution-and-auth.md` データフロー節）で、初回OIDCログイン成功時に `active` へ遷移させる（上記コードの2番目の分岐）。

##### 3.6 セッションJWT（`app/security/session.py`）

`stream_tickets.py` と同じ `jwt.encode`/`jwt.decode`（HS256）パターン。ペイロードは `{"sub": str(app_user_id), "iat": ..., "exp": ...}`。`session_signing_secret` で署名し、Cookie名 `session`、属性 `HttpOnly=True, Secure=True, SameSite="strict", max_age=session_ttl_seconds` で返す。`Secure=True` はローカル開発でHTTPS無しの場合に問題になるため、`settings.app_env == "local"` の時だけ `Secure=False` にする分岐を入れる（既存の `docs/plans/local-launcher.md` がローカル開発をHTTPで想定している前提に合わせる）。

##### 3.7 `/auth/*` エンドポイント（`app/api/routes/auth.py`）

- `GET /auth/login`: discovery document取得 → PKCEペア生成 → `state`/`nonce`生成 → 認可URLへ307リダイレクト。`oidc_state` Cookie発行。
- `GET /auth/callback?code=...&state=...`: `oidc_state` Cookie検証 → token交換 → `id_token`検証 → `AppUser`解決 → セッションJWT発行しCookie設定 → フロントエンドのトップページへリダイレクト。
- `POST /auth/logout`: `session` Cookieを削除するのみ（3.1節の制約どおり、サーバー側失効はしない）。
- `GET /auth/me`: `require_authenticated_user` 依存を通し、現在のユーザー情報（`id`, `email`, `display_name`）と、所属ワークスペース一覧（`user_membership` をJOIN）を返す。フロントエンドがログイン状態・利用可能ワークスペースを判定するために使う。

##### 3.8 `require_authenticated_user`（`app/security/rbac.py`）

```python
def require_authenticated_user(
    db: Annotated[Session, Depends(get_db)],
    session: Annotated[str | None, Cookie()] = None,
) -> AppUser:
    if session is None or settings.session_signing_secret is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        claims = verify_session_token(
            session, secret=settings.session_signing_secret.get_secret_value()
        )
    except SessionTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated") from exc
    user = db.get(AppUser, claims.app_user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user
```

（`db` をデフォルト値のない`session`より前に置いているのは、Python関数定義でデフォルト値を持つ引数は持たない引数より後ろに置く必要があるため。`verify_session_token` が `SessionTokenError`（`app/security/session.py` で `StreamTicketError` と同じ形で定義する例外）を送出する設計は `stream_tickets.py` の `verify_and_consume_ticket` に揃える。)

#### テスト方針
- `tests/test_oidc_client.py`: `respx`（既存devの依存、`httpx` のモック用）を使い、discovery/token交換のHTTPリクエストをモックした結合テスト。PKCEの `code_verifier`/`code_challenge` 対応が正しいこと、`state` 不一致時に拒否されること、を確認する。
- `tests/test_session_token.py`: セッションJWTの発行・検証・期限切れ・改竄検出（`stream_tickets.py` の既存テストと同型）。
- `tests/test_auth_api.py`: `/auth/login` が正しい認可URLへリダイレクトすること、`/auth/callback` の正常系・異常系（`state`不一致、IdPエラー、無効なIDトークン、`disabled`ユーザー）、`/auth/me` の認証要否。

#### 想定リスク
- 中。外部IdPとの実結合試験はCIでは行えない（モックのみ）。実IdP（顧客が用意するもの）との結合確認は導入手順書（Unit 10）でのマニュアル確認手順として案内する。
- セッションJWTのサーバー側強制失効ができない制約（2.1節）は、`docs/architecture-alignment-and-long-term-roadmap.md` の完了条件「権限境界、CSRF/CORS、session、secret rotation、監査ログのsecurity testが通る」との整合を実装者が意識すること。将来的に即時失効が要件化した場合は、`jti` をDBに記録し失効リストと突き合わせる方式への変更が必要（本Unitのスコープ外）。

---

### Unit 4: RBAC認可依存を既存APIへ適用

#### 目的
`require_owner` を全APIルートから撤去し、`require_authenticated_user` + ワークスペースロールチェックへ置き換える。

#### 変更対象ファイル・新規作成ファイル

- **`app/security/rbac.py`**（Unit 3で作成済み、本Unitで追記）: `require_workspace_role(minimum_role)` を追加。
- **`app/security/auth.py`**: `require_owner` 関数と、それが依存する `settings.dev_owner_token` の使用箇所を削除する。
- **`app/core/config.py`**: `dev_owner_token: SecretStr | None = None` フィールドを削除する。
- **`.env.example`**: `DEV_OWNER_TOKEN` の記載を削除する。
- **`app/api/routes/workspaces.py`**, **`connections.py`**, **`instruments.py`**, **`market_data.py`**, **`client_logs.py`**: 全`Owner = Annotated[str, Depends(require_owner)]` を、エンドポイントごとに適切な依存へ置き換える。

```python
_ROLE_SEVERITY = {"viewer": 1, "operator": 2, "owner": 3}


def require_workspace_role(minimum_role: str) -> Callable[..., AppUser]:
    def _dependency(
        workspace_id: UUID,
        db: Annotated[Session, Depends(get_db)],
        current_user: Annotated[AppUser, Depends(require_authenticated_user)],
    ) -> AppUser:
        membership = db.scalar(
            select(UserMembership).where(
                UserMembership.workspace_id == workspace_id,
                UserMembership.user_id == current_user.id,
            )
        )
        if membership is None or _ROLE_SEVERITY[membership.role] < _ROLE_SEVERITY[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient workspace role"
            )
        return current_user

    return _dependency


def require_any_workspace_owner(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[AppUser, Depends(require_authenticated_user)],
) -> AppUser:
    """Unit 5 (`/service-accounts`)向け。特定のworkspace_idを持たないグローバル
    リソースのため`require_workspace_role`は使えない。いずれか1つ以上の
    workspaceでownerであれば許可する（`user_membership`に'owner'の行が
    1件でも存在するか、を見るだけの軽量チェック）。"""
    has_ownership = db.scalar(
        select(UserMembership.workspace_id)
        .where(UserMembership.user_id == current_user.id, UserMembership.role == "owner")
        .limit(1)
    )
    if has_ownership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Workspace owner role required"
        )
    return current_user
```

各ルートファイルでの使い方（`workspaces.py` の例。既存の `Owner = Annotated[str, Depends(require_owner)]` を削除し、次を追加）:

```python
Viewer = Annotated[AppUser, Depends(require_workspace_role("viewer"))]
Operator = Annotated[AppUser, Depends(require_workspace_role("operator"))]
Owner = Annotated[AppUser, Depends(require_workspace_role("owner"))]
AnyAuthenticatedUser = Annotated[AppUser, Depends(require_authenticated_user)]
```

#### 4.1 エンドポイント別の必要ロール（本計画が決定した割当て。実装エージェントは変更せずそのまま適用する）

| ファイル | メソッド + パス | 必要ロール | 備考 |
|---|---|---|---|
| workspaces.py | `POST /workspaces` | `AnyAuthenticatedUser` | 作成と同時に作成者を`owner`として`user_membership`へ登録する（下記4.2） |
| workspaces.py | `GET /workspaces` | `AnyAuthenticatedUser` | 返す一覧を「自分が所属するワークスペースのみ」に絞る（挙動変更、2.2節参照） |
| workspaces.py | `GET /workspaces/{workspace_id}` | `Viewer` | |
| connections.py | `GET /workspaces/{workspace_id}/connections` | `Viewer` | |
| connections.py | `POST /workspaces/{workspace_id}/connections` | `Operator` | |
| connections.py | `POST /workspaces/{workspace_id}/connections/{id}/disable` | `Operator` | |
| connections.py | `DELETE /workspaces/{workspace_id}/connections/{id}` | `Owner` | 破壊的操作のため |
| connections.py | `PUT /workspaces/{workspace_id}/connections/{id}/credentials` | `Operator` | |
| connections.py | `POST /workspaces/{workspace_id}/connections/{id}/verify` | `Operator` | |
| connections.py | `GET /workspaces/{workspace_id}/accounts` | `Viewer` | |
| connections.py | `PUT /workspaces/{workspace_id}/account-selections/{code}` | `Operator` | |
| connections.py | `GET /exchanges` | `AnyAuthenticatedUser` | ワークスペース非依存のグローバルカタログ |
| connections.py | `GET /markets` | `AnyAuthenticatedUser` | 同上 |
| instruments.py | `GET /workspaces/{workspace_id}/instruments` | `Viewer` | |
| instruments.py | `POST /workspaces/{workspace_id}/instruments/sync` | `Operator` | |
| market_data.py | `POST /workspaces/{workspace_id}/candle-backfills` | `Operator` | |
| market_data.py | `GET /workspaces/{workspace_id}/candle-backfills` | `Viewer` | |
| market_data.py | `GET /workspaces/{workspace_id}/instruments/{id}/candles` | `Viewer` | |
| market_data.py | `GET /workspaces/{workspace_id}/instruments/{id}/candle-coverage` | `Viewer` | |
| market_data.py | `PUT /workspaces/{workspace_id}/market-data-subscription` | `Operator` | |
| market_data.py | `PUT /workspaces/{workspace_id}/market-data-subscriptions` | `Operator` | |
| market_data.py | `GET /workspaces/{workspace_id}/market-data-subscriptions` | `Viewer` | |
| market_data.py | `POST /workspaces/{workspace_id}/market-stream-tickets` | `Viewer` | 閲覧目的のリアルタイム購読のため |
| client_logs.py | `POST /client-logs` | `AnyAuthenticatedUser` | `workspace_id`を取らないため、ロールチェック対象外 |

#### 4.2 ワークスペース作成時の所有者登録

`create_workspace`（`workspaces.py`）内で、`Workspace` を `db.add` した直後に同一トランザクションで次を追加する。

```python
db.add(workspace)
db.flush()
db.add(UserMembership(workspace_id=workspace.id, user_id=current_user.id, role="owner"))
db.commit()
```

#### 4.3 `GET /workspaces` の絞り込み

```python
def list_workspaces(db: DatabaseSession, current_user: AnyAuthenticatedUser) -> list[Workspace]:
    statement = (
        select(Workspace)
        .join(UserMembership, UserMembership.workspace_id == Workspace.id)
        .where(UserMembership.user_id == current_user.id)
        .order_by(Workspace.created_at)
    )
    return list(db.scalars(statement).all())
```

#### テスト方針
- 既存の `tests/test_workspaces_api.py`, `test_connections_api.py`, `test_instrument_api.py`, `test_market_data_api.py`, `test_client_logs_api.py` の `override_database` ヘルパー（`app.dependency_overrides[require_owner] = ...`）を、`require_authenticated_user` と `require_workspace_role` のオーバーライドに書き換える。
- 新規 `tests/test_rbac.py`: ロール別のマトリクステスト。`viewer`が書き込み系エンドポイントで403になること、`operator`が`owner`専用エンドポイント（`DELETE .../connections/{id}`）で403になること、他ワークスペースの`owner`が別ワークスペースへアクセスすると403（または404、対象存在確認の順序次第）になることを、エンドポイントごとに網羅する。
- `tests/test_workspaces_api.py` に、ワークスペース作成時に `user_membership` へ `owner` 行が作られることを確認するテストを追加する。

#### 想定リスク
- 高。22エンドポイント全てに影響する変更で、既存テストの大半が書き換えを必要とする。1ファイルずつ（workspaces.py → connections.py → instruments.py → market_data.py → client_logs.py の順を推奨、依存が少ない順）移行し、都度 `python -m pytest` を実行してから次へ進むこと。
- `GET /workspaces` の挙動変更はfrontendに影響する可能性がある（2.2節参照）。

---

### Unit 5: System Worker サービスアカウント機構

#### 目的
バックグラウンドワーカー（Market Data Worker、Unit 8で追加するNotification Worker）がAPIではなく直接DBへ接続する現行方式を変えない前提で、**将来ワーカーがAPI経由で操作する必要が生じた場合**に備えたサービスアカウント認証の基盤を用意する。`user_membership`のCHECK制約（owner/operator/viewerのみ）を変更せず、別テーブルとして新設する。

#### 新規作成ファイル

1. **`alembic/versions/<実装日>_0008_service_account.py`**（`down_revision = "20260920_0007"`。実装日時点のYYYYMMDD、リビジョン番号は0008から連番で採番すること）

```python
"""Create fx.service_account: API-key-based identity for background workers
(distinct from user_membership's owner/operator/viewer -- a service account
is not a human workspace member, and user_membership's CHECK constraint does
not include a 'system_worker' value; see
docs/plans/horizon5-implementation-plan.md Unit 5).

The raw API key is never stored -- only a SHA-256 hash, mirroring how
app/security/auth.py's require_owner already compares tokens with
secrets.compare_digest rather than storing them reversibly. A short
key_prefix (first 8 chars of the raw key) is stored in the clear so lookup
does not require hashing every row on each request.
"""

from alembic import op

revision = "<実装日>_0008"
down_revision = "20260920_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE fx.service_account (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name text NOT NULL,
          key_prefix text NOT NULL,
          key_hash text NOT NULL,
          status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
          created_by uuid REFERENCES fx.app_user(id) ON DELETE SET NULL,
          created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
          revoked_at timestamptz,
          CONSTRAINT uq_service_account_key_prefix UNIQUE (key_prefix),
          CONSTRAINT ck_service_account_revoked CHECK (
            (status <> 'revoked' AND revoked_at IS NULL)
            OR (status = 'revoked' AND revoked_at IS NOT NULL)
          )
        );
        """
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("DROP TABLE fx.service_account;")
```

2. **`app/models/service_account.py`** — `ServiceAccount` ORM（上記DDLに対応する `Mapped` 定義。既存モデルの書き方に合わせる）。
3. **`app/security/service_account.py`** — キー生成・ハッシュ照合。

```python
import hashlib
import secrets

_KEY_PREFIX_LENGTH = 8


def generate_service_account_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_prefix, key_hash). raw_key is shown to the
    operator exactly once and never persisted; only key_prefix/key_hash are
    stored (see migration docstring)."""
    raw_key = secrets.token_urlsafe(32)
    key_prefix = raw_key[:_KEY_PREFIX_LENGTH]
    key_hash = hashlib.sha256(raw_key.encode("ascii")).hexdigest()
    return raw_key, key_prefix, key_hash


def verify_service_account_key(raw_key: str, *, expected_hash: str) -> bool:
    candidate_hash = hashlib.sha256(raw_key.encode("ascii")).hexdigest()
    return secrets.compare_digest(candidate_hash, expected_hash)
```

SHA-256を選ぶ理由（bcrypt/argon2のような低速ハッシュを使わない理由）: `raw_key` は `secrets.token_urlsafe(32)` による256ビットの高エントロピーランダム値であり、人間が選ぶ低エントロピーなパスワードとは異なりオフライン総当たりの実用的なリスクがないため、低速化は不要（`app/security/auth.py` の既存トークン比較方針と同じ考え方）。

4. **`app/api/routes/service_accounts.py`** — `Owner`ロールのみが叩ける管理エンドポイント。
   - `POST /service-accounts`: 発行。レスポンスで `raw_key` を一度だけ返す（DBには保存しない）。`service_account` テーブルはワークスペース非依存（システム全体のワーカー用）のため、パスに `workspace_id` を含めない。依存関数はUnit 4で `app/security/rbac.py` に追加した `require_any_workspace_owner`（4.1節末尾のコード）を使う。
   - `POST /service-accounts/{id}/revoke`: 失効。
   - `GET /service-accounts`: 一覧（`raw_key`/`key_hash`は返さない）。

#### 変更対象ファイル
- `app/api/router.py`: `service_accounts.router` を登録。

#### テスト方針
- `tests/test_service_account_model.py`（専用PostgreSQL使用、Unit 1と同じ方式）: CHECK制約、`uq_service_account_key_prefix`の一意性。
- `tests/test_service_account_auth.py`: `generate_service_account_key`/`verify_service_account_key`の往復、改竄されたキーの拒否。
- `tests/test_service_accounts_api.py`: 発行・失効・一覧のAPIレベルテスト、`Owner`以外からのアクセス拒否。

#### 想定リスク
中。本Unitは「将来ワーカーがAPI経由で操作する必要が生じた場合」の基盤整備であり、Market Data Worker自体（`app/market_data/worker/`）を本Unitでこの認証方式に移行するわけではない（現行のDB直結方式のまま据え置く）。この点を実装エージェントは混同しないこと。Notification Worker（Unit 8）も同様にDB直結とし、本Unitのサービスアカウントは使わない（過剰実装を避けるため）。

---

### Unit 6: オフライン検証ライセンスキー機構

#### 目的
運営者が常時稼働のアクティベーションサーバーを持たずに、顧客が自分の環境で実行するソフトウェアのライセンスをオフラインで検証できるようにする。

#### 新規作成ファイル

1. **`scripts/generate_license_signing_keypair.py`** — 運営者が一度だけ手元で実行するCLI（アプリ本体には含めない、Gitにコミットしない秘密鍵を生成する）。

```python
"""One-time operator tool: generates the Ed25519 keypair used to sign
license files. Run locally, never in CI or on a customer's machine. The
private key must be saved outside this repository (e.g. a password
manager); only the public key is meant to be committed, by pasting it into
app/licensing/public_key.py."""

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

private_key = Ed25519PrivateKey.generate()
public_key = private_key.public_key()

private_raw = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
public_raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)

print("PRIVATE KEY (save outside the repo, never commit):")
print(base64.b64encode(private_raw).decode("ascii"))
print()
print("PUBLIC KEY (paste into app/licensing/public_key.py):")
print(base64.b64encode(public_raw).decode("ascii"))
```

[cryptography公式: Ed25519](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/) の `generate()` / `private_bytes()` / `public_bytes()` API（インストール済みcryptography 46.0.7で提供、Ed25519 APIは長期間安定している）。

2. **`app/licensing/__init__.py`**
3. **`app/licensing/public_key.py`** — 実装時点ではプレースホルダを置き、リリース準備時に運営者が実際の公開鍵へ差し替える。

```python
"""Public key used to verify license files (Ed25519, raw 32 bytes,
base64-encoded). Generated once via scripts/generate_license_signing_keypair.py.
Not a secret -- safe to commit."""

LICENSE_PUBLIC_KEY_B64 = "REPLACE_BEFORE_FIRST_RELEASE"
```

4. **`app/licensing/verification.py`**

```python
import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class LicenseError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LicenseClaims:
    license_id: str
    customer: str
    issued_at: datetime
    expires_at: datetime | None
    features: tuple[str, ...]

    def is_expired(self, *, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at


def _canonical_payload_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_license_document(document: dict[str, object], *, public_key_b64: str) -> LicenseClaims:
    try:
        payload = document["payload"]
        signature = base64.b64decode(document["signature"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseError("license_malformed", "License file is malformed") from exc

    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    try:
        public_key.verify(signature, _canonical_payload_bytes(payload))
    except InvalidSignature as exc:
        raise LicenseError("license_signature_invalid", "License signature is invalid") from exc

    try:
        return LicenseClaims(
            license_id=payload["license_id"],
            customer=payload["customer"],
            issued_at=datetime.fromisoformat(payload["issued_at"]),
            expires_at=(
                datetime.fromisoformat(payload["expires_at"])
                if payload.get("expires_at") is not None
                else None
            ),
            features=tuple(payload.get("features", [])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseError("license_malformed", "License file is malformed") from exc
```

[cryptography公式: Ed25519 — verify()](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/) — 署名不正時は `cryptography.exceptions.InvalidSignature` を送出する仕様。

5. **`scripts/issue_license.py`** — 運営者用CLI（顧客ごとにライセンスファイルを発行）。秘密鍵は環境変数 `LICENSE_SIGNING_PRIVATE_KEY_B64`（このCLI実行時のみ使用、アプリ本体の設定には追加しない）から読む。署名は `private_key.sign(message)`（Ed25519、64バイト、[cryptography公式: Ed25519 — sign()](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)）。

```python
"""Operator CLI: issues one signed license file for one customer.
Usage: LICENSE_SIGNING_PRIVATE_KEY_B64=<base64> python scripts/issue_license.py \
    --license-id <uuid> --customer "Acme Inc" --expires-at 2027-09-20T00:00:00+00:00 \
    --feature core --output license.json
Omit --expires-at for a perpetual (non-expiring) license.
"""

import argparse
import base64
import json
import os
import sys
from datetime import UTC, datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _canonical_payload_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--license-id", required=True)
    parser.add_argument("--customer", required=True)
    parser.add_argument("--expires-at", default=None, help="ISO 8601, omit for perpetual")
    parser.add_argument("--feature", action="append", default=[], dest="features")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    raw_private_key = os.environ.get("LICENSE_SIGNING_PRIVATE_KEY_B64")
    if not raw_private_key:
        print("LICENSE_SIGNING_PRIVATE_KEY_B64 is not set", file=sys.stderr)
        return 1
    private_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw_private_key))

    payload: dict[str, object] = {
        "license_id": args.license_id,
        "customer": args.customer,
        "issued_at": datetime.now(UTC).isoformat(),
        "expires_at": args.expires_at,
        "features": args.features,
    }
    signature = private_key.sign(_canonical_payload_bytes(payload))
    document = {"payload": payload, "signature": base64.b64encode(signature).decode("ascii")}

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`Ed25519PrivateKey.from_private_bytes()` は生成スクリプト（上記4.）が出力したraw 32バイト鍵をbase64デコードしたものを受け取る（[cryptography公式: Ed25519 — from_private_bytes()](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)）。

6. **`app/licensing/dependency.py`** — 起動時1回検証 + リクエスト毎の期限チェック。

```python
import json
from datetime import UTC, datetime
from functools import lru_cache

import structlog
from fastapi import HTTPException, status

from app.core.config import settings
from app.licensing.public_key import LICENSE_PUBLIC_KEY_B64
from app.licensing.verification import LicenseClaims, LicenseError, verify_license_document

logger = structlog.get_logger("app.licensing")


@lru_cache
def _load_license() -> LicenseClaims | None:
    """Verified once per process lifetime (mirrors get_default_feed_hub()'s
    lru_cache singleton pattern). Returns None if the license file is
    missing or invalid -- every request is then rejected by
    require_valid_license below, but the process itself still starts (see
    docs/plans/horizon5-implementation-plan.md Unit 6 for why this
    is 503-per-request rather than a hard process exit: consistent with how
    app/security/auth.py and app/services/secrets.py already return 503 for
    an unconfigured setting instead of crashing)."""
    try:
        with settings.license_file_path.open(encoding="utf-8") as handle:
            document = json.load(handle)
        return verify_license_document(document, public_key_b64=LICENSE_PUBLIC_KEY_B64)
    except (FileNotFoundError, LicenseError, ValueError) as exc:
        logger.error("license_invalid", reason=str(exc))
        return None


def require_valid_license() -> None:
    claims = _load_license()
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="License is missing or invalid"
        )
    if claims.is_expired(now=datetime.now(UTC)):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="License has expired")
```

`verify_license_document`の`payload`引数は`document["payload"]`（`dict[str, object]`からの取り出しのため静的には`object`型）をそのまま渡している。`python -m mypy`を通すため、実装時は`isinstance(payload, dict)`のガード、またはpydanticモデル（`app/schemas/`の既存`OrmModel`と同様のBaseModel）でのパースに置き換え、`payload["license_id"]`等の添字アクセスがmypyにとって安全な型から行われるようにすること。

#### 変更対象ファイル
- `app/core/config.py`: `license_file_path: Path = Path("license.json")` を追加。
- `app/main.py`: `app.include_router(api_router, prefix=settings.api_v1_prefix, dependencies=[Depends(require_valid_license)])` のように、`api_router` を含める箇所へ `dependencies=[Depends(require_valid_license)]` を追加する（`/health`, `/health/db` はこの対象外のまま — 死活監視のためライセンス無効時でも200を返せる必要がある）。`app.include_router(market_stream_ws.router)`（WebSocket）は`api_router`とは別に登録されているため、このライセンスチェックの対象外になる。ただしWebSocket接続には`api_router`経由（ライセンスチェック対象）の`/market-stream-tickets`エンドポイントで発行された短命チケットが必須のため、ライセンス無効時でも新規チケットは発行されず、実質的に新規接続はできない。実装エージェントはこの間接的な防御で十分かを判断し、不十分と判断する場合のみ`market_stream_ws.router`側にも同等のチェックを追加すること。
- `.env.example`: `LICENSE_FILE_PATH=license.json` の説明を追加。
- `.gitignore`: `license.json` を追加（顧客固有の実ファイルを誤ってコミットしないため）。

#### テスト方針
- `tests/test_license_verification.py`: 正常署名/改竄されたペイロード/改竄された署名/期限切れ/期限なし(perpetual)/不正な公開鍵の各ケース。テスト用に都度Ed25519鍵ペアを生成し、本番の `LICENSE_PUBLIC_KEY_B64` には依存しない形で書く。
- `tests/test_license_dependency.py`: `require_valid_license` が有効なライセンスで通過し、無効で503を返すこと。`app.dependency_overrides` でファイルシステム読み込みを避ける（`_load_license` 自体をオーバーライド対象にできるよう、`lru_cache` されたモジュールレベル関数ではなく、テストでは `app/licensing/dependency.py` の `_load_license.cache_clear()` を呼んでから一時ファイルを指す `settings.license_file_path` を差し替える）。

#### 想定リスク
- 秘密鍵の管理はUnit外（運営者の手順、Unit 10の運用ドキュメントに記載する）。本Unitのコードには秘密鍵を一切含めない。
- `lru_cache` によるプロセス起動時1回検証のため、稼働中にライセンスファイルを更新しても再起動するまで反映されない。この制約はUnit 10の運用ドキュメントに明記すること。

---

### Unit 7: Secretバックエンドのプラガブル化（AWS Secrets Manager/KMS実装を例として追加）

#### 目的
Unit 2で切り出した `SecretStore` Protocolに対し、AWS環境向けの実装を1つ追加する。GCP/Vaultは本計画のスコープ外（0.3節）。

#### 新規作成ファイル

1. **`app/services/secrets_backends/__init__.py`**
2. **`app/services/secrets_backends/aws_secrets_manager.py`**

`put`/`get`/`delete`（辞書全体の秘密情報、例: 取引所APIキー一式）はAWS Secrets Managerへ、`encrypt_text`/`decrypt_text`（単一文字列の封筒暗号化）はAWS KMSへ、と役割を分ける。理由: Secrets Managerは「名前付きシークレットの保管」に向くが呼び出しコスト・レート制限があり、`encrypt_text`/`decrypt_text` のような単発の暗号化/復号にはKMSの `Encrypt`/`Decrypt` API（呼び出し元がciphertextを自分のDBへ保存する方式）の方が[boto3公式のユースケースに合致する](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/kms.html#KMS.Client.encrypt)。

```python
import base64
import json
from pathlib import Path
from uuid import uuid4

import boto3


class AwsSecretsManagerStore:
    """SecretStore implementation backed by AWS Secrets Manager (put/get/delete)
    and AWS KMS (encrypt_text/decrypt_text). See
    docs/plans/horizon5-implementation-plan.md Unit 7 for why these
    two AWS services are split this way rather than using Secrets Manager
    for everything."""

    def __init__(self, *, kms_key_id: str, secret_name_prefix: str = "ai-system-trading/") -> None:
        self._secrets_client = boto3.client("secretsmanager")
        self._kms_client = boto3.client("kms")
        self._kms_key_id = kms_key_id
        self._secret_name_prefix = secret_name_prefix

    def put(self, values: dict[str, str]) -> str:
        secret_id = uuid4().hex
        name = f"{self._secret_name_prefix}{secret_id}"
        self._secrets_client.create_secret(Name=name, SecretString=json.dumps(values, sort_keys=True))
        return f"aws-secrets-manager://{name}"

    def get(self, secret_ref: str) -> dict[str, str]:
        name = self._name_from_ref(secret_ref)
        response = self._secrets_client.get_secret_value(SecretId=name)
        return json.loads(response["SecretString"])

    def delete(self, secret_ref: str) -> None:
        name = self._name_from_ref(secret_ref)
        self._secrets_client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)

    def encrypt_text(self, value: str) -> str:
        response = self._kms_client.encrypt(KeyId=self._kms_key_id, Plaintext=value.encode("utf-8"))
        return base64.b64encode(response["CiphertextBlob"]).decode("ascii")

    def decrypt_text(self, value: str) -> str:
        ciphertext = base64.b64decode(value)
        response = self._kms_client.decrypt(CiphertextBlob=ciphertext, KeyId=self._kms_key_id)
        return response["Plaintext"].decode("utf-8")

    def _name_from_ref(self, secret_ref: str) -> str:
        prefix = "aws-secrets-manager://"
        if not secret_ref.startswith(prefix):
            raise ValueError("Unsupported secret reference")
        return secret_ref.removeprefix(prefix)
```

API呼び出しは [boto3 Secrets Manager クライアントリファレンス](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/secretsmanager.html)（`create_secret`, `get_secret_value`, `delete_secret`）と [boto3 KMS クライアントリファレンス](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/kms.html)（`encrypt`, `decrypt`）に準拠。

#### 変更対象ファイル
- `pyproject.toml`: 新規optional-dependencies groupを追加（コア依存には含めない。AWSを使わない顧客に不要なSDKを強制しないため）。

```toml
[project.optional-dependencies]
aws = ["boto3>=1.43,<2"]
```

（2026-09-20時点のPyPI最新は1.43.98。上限はメジャーバージョン固定のみとし、既存の他依存と同じ運用にする。）

**`aws` extraは`dev` extraに含めない（AWSを使わない開発者に不要なSDKを強制しないため）が、CIの`backend`ジョブは`pip install -e ".[dev]"`のみを実行し`boto3`をインストールしない一方、`python -m mypy`は`files = ["app", "scripts"]`設定によりboto3をインストールしていない環境でも`app/services/secrets_backends/aws_secrets_manager.py`を静的検査対象にする。このままでは`import boto3`でmypyが失敗するため、既存の`binance.*`/`oandapyV20.*`と同じ形で`pyproject.toml`に次のoverrideを追加すること（これを追加し忘れると本Unit実装後にCIの`Type check Python`ステップが失敗する）。**

```toml
[[tool.mypy.overrides]]
module = ["boto3", "botocore.*"]
ignore_missing_imports = true
```

- `app/services/secrets.py`: `get_secret_store()` を、環境変数 `SECRET_STORE_BACKEND`（既定 `"local"`）の値に応じて `LocalEncryptedSecretStore` か `AwsSecretsManagerStore` を返すよう分岐させる。`AwsSecretsManagerStore` のimportは関数内（遅延import）にし、`boto3` 未インストール環境（`aws` extra未導入）でも `local` 運用ではエラーにならないようにする。
- `app/core/config.py`: `secret_store_backend: str = "local"`, `aws_kms_key_id: str | None = None` を追加。
- `.env.example`: 上記2設定を追記。

#### テスト方針
- `tests/test_aws_secrets_manager_store.py`: `boto3` の `botocore.stub.Stubber`（boto3公式のテスト用スタブ機構、[boto3公式: Stubber](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/stubber.html)）を使い、実AWS呼び出し無しでAPI呼び出しパラメータと戻り値マッピングを検証する。`pyproject.toml` の `dev` extraには影響しない（`aws` extraを別途 `pip install -e ".[dev,aws]"` した環境でのみ実行できるテストとし、`tests/test_aws_secrets_manager_store.py` の先頭で `pytest.importorskip("boto3")` を使い、未インストール環境（通常のCI）ではスキップされるようにする。
- CI（`ci.yml`）への追加は不要（スキップされるため）。ただし `docs/plans/ci-quality-gate-hardening.md` の精神に反しない範囲で、将来AWS実装を主要パスにする場合は別途 `aws` extra込みのCIジョブ追加を検討する（本計画のスコープ外として明記する）。

#### 想定リスク
低〜中。実AWS環境との結合試験は行わない（スタブのみ）。実運用前に顧客側で実AWS環境に対する動作確認が必要（Unit 10の運用ドキュメントに記載）。

---

### Unit 8: Notification / Outbox

#### 目的
既存のDBスキーマ（`notification`, `outbox_event`, `system_event`）に対応するORMを追加し、汎用SMTPアダプタとNotification Workerを実装する。

#### 変更対象ファイル
- **`app/models/audit.py`**: `OutboxEvent` クラスを追加（`SystemEvent` と同じファイル。両方とも「システム全体のイベント」という同じ関心事のため）。

```python
class OutboxEvent(Base):
    __tablename__ = "outbox_event"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    aggregate_type: Mapped[str] = mapped_column(Text)
    aggregate_id: Mapped[UUID]
    event_type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    correlation_id: Mapped[UUID]
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    attempts: Mapped[int] = mapped_column(server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

#### 新規作成ファイル

1. **`app/models/notifications.py`** — `Notification` ORM（`notification` テーブル、603-616行目のカラムに1:1対応）。

```python
class Notification(Base):
    __tablename__ = "notification"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.workspace.id", ondelete="CASCADE")
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.system_event.id", ondelete="CASCADE")
    )
    channel: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    recipient_ref: Mapped[str] = mapped_column(Text)
    delivery_attempts: Mapped[int] = mapped_column(server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.app_user.id"))
```

（`from datetime import datetime`、`from uuid import UUID`、`from sqlalchemy import DateTime, ForeignKey, Text, func`、`from sqlalchemy.orm import Mapped, mapped_column`、`from app.core.config import settings`、`from app.db.session import Base`、`SCHEMA = settings.database_schema` は既存モデルファイル（`app/models/audit.py`等）と同じ書き方で先頭に置く。）

2. **`app/notifications/__init__.py`**
3. **`app/notifications/adapters/__init__.py`**
4. **`app/notifications/adapters/base.py`**

```python
from typing import Protocol


class NotificationAdapter(Protocol):
    def send(self, *, recipient: str, subject: str, body: str) -> None: ...
```

5. **`app/notifications/adapters/smtp.py`** — 標準ライブラリ `smtplib` + `email.message.EmailMessage`（[Python公式: smtplib](https://docs.python.org/3/library/smtplib.html)、[Python公式: email.message.EmailMessage](https://docs.python.org/3/library/email.message.html#email.message.EmailMessage)）。新規外部依存は不要。

```python
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    use_tls: bool
    sender_address: str


class SmtpNotificationAdapter:
    def __init__(self, config: SmtpConfig) -> None:
        self._config = config

    def send(self, *, recipient: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._config.sender_address
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(self._config.host, self._config.port, timeout=10) as client:
            if self._config.use_tls:
                client.starttls()
            if self._config.username and self._config.password:
                client.login(self._config.username, self._config.password)
            client.send_message(message)
```

6. **`app/notifications/application/deliver_notifications.py`** — Outboxポーリング + 配信。

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import OutboxEvent
from app.models.notifications import Notification
from app.notifications.adapters.base import NotificationAdapter

_BATCH_SIZE = 50


def claim_pending_outbox_events(db: Session) -> list[OutboxEvent]:
    """SELECT ... FOR UPDATE SKIP LOCKED so multiple Notification Worker
    processes can run concurrently without double-delivering the same event
    (SQLAlchemy 2.0: Select.with_for_update(skip_locked=True), see
    https://docs.sqlalchemy.org/en/20/orm/queryguide/select.html#selecting-for-update ).
    Deliberately not the market-data worker's lease/heartbeat machinery: that
    exists for long-running fetch jobs that can crash mid-flight and need
    stale-recovery, whereas a notification send is a single short call that
    either finishes or fails within one transaction."""
    statement = (
        select(OutboxEvent)
        .where(OutboxEvent.status == "pending")
        .order_by(OutboxEvent.available_at)
        .limit(_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    return list(db.scalars(statement).all())


def deliver_pending_notifications(
    db: Session, *, adapter: NotificationAdapter, recipient_resolver
) -> int:
    """recipient_resolver(event: OutboxEvent) -> list[tuple[str, str]] returns
    (channel, recipient_ref) pairs for who should be notified about this
    event; left as an injected callback since the actual workspace/user
    targeting policy is a product decision out of this Unit's scope."""
    delivered = 0
    for event in claim_pending_outbox_events(db):
        recipients = recipient_resolver(event)
        for _channel, recipient_ref in recipients:
            notification = Notification(
                workspace_id=event.aggregate_id,  # see caveat below
                event_id=event.id,
                channel="email",
                recipient_ref=recipient_ref,
                status="queued",
            )
            db.add(notification)
            try:
                adapter.send(recipient=recipient_ref, subject=event.event_type, body=str(event.payload))
                notification.status = "sent"
            except OSError:
                notification.status = "failed"
                notification.delivery_attempts += 1
        event.status = "published"
        db.flush()
        delivered += 1
    db.commit()
    return delivered
```

`Notification.workspace_id` の解決方法（上記コード中 `# see caveat below`）は `aggregate_type` によって異なる可能性があるため、実装時に `outbox_event.aggregate_type` の実際の値一覧（本計画作成時点でまだ発生源となるドメインイベントの発行コードが存在しないため未確定）を確認し、`aggregate_id` から `workspace_id` を正しく解決するロジックへ具体化すること。**この関数はスケルトンであり、そのまま使わない。**

7. **`app/notifications/worker/main.py`** — 単純なポーリングループ（`app/market_data/worker/` のような専用lease機構は使わない、理由は上記コメントのとおり）。`app/core/config.py` に `notification_poll_interval_seconds: float = 5.0` を追加し、`time.sleep` ベースの同期ループ、または既存Market Data Workerが使っているのと同様の非同期パターンのいずれかを、実装時に既存 `app/market_data/worker/` の起動方式（`scripts/start_local.py` がどう起動するか）を確認したうえで揃えること。

#### 変更対象ファイル（続き）
- `app/core/config.py`: `smtp_host`, `smtp_port`, `smtp_username`, `smtp_password (SecretStr)`, `smtp_use_tls: bool = True`, `smtp_sender_address`, `notification_poll_interval_seconds` を追加。
- `.env.example`: 上記を追記。
- `scripts/start_local.py`: Notification Workerを起動対象へ追加するかどうかは、既存ファイルの構造（Market Data WorkerをどうR/A/Qキー操作に組み込んでいるか、`docs/plans/durable-market-data-worker.md` 参照)を確認してから決定すること。**本計画では起動方法の具体的なコード変更までは指定しない**（既存の起動スクリプトの設計を壊さないことを優先し、実装エージェントの裁量とする）。

#### テスト方針
- `tests/test_notification_models.py`（専用PostgreSQL）: `Notification`/`OutboxEvent` ORMの永続化・DB CHECK制約確認。
- `tests/test_smtp_adapter.py`: `smtplib.SMTP` をモック（`unittest.mock.patch("smtplib.SMTP")`）し、`EmailMessage` のFrom/To/Subject/本文が正しく組み立てられること。
- `tests/test_deliver_notifications.py`: `claim_pending_outbox_events` が `pending` のみを対象にし、既に`published`のものを対象外とすること。`FOR UPDATE SKIP LOCKED` の並行安全性は、専用PostgreSQL上で2セッション同時実行するテスト（`tests/test_worker_leases_postgres.py` の同時実行テストパターンを参考にする）で確認する。

#### 想定リスク
高。本Unitはスケルトン実装であり、`aggregate_type`/`aggregate_id`からの実際の宛先解決ロジックが未確定（ドメインイベントの発行元コード自体がまだ存在しないため）。実装エージェントは、着手前に「どのドメインイベントを最初に配線するか」（例: `connection.credentials_updated`の監査ログをトリガーに通知するのか、trading_haltの発動を通知するのか）を利用者に確認すること。本計画はUnit 8を「基盤（ORM・アダプタ・配信ループの骨格）を用意するところまで」と定義し、具体的なイベント種別ごとの配線は別タスクとする。

---

### Unit 9: リリースゲート強化（SBOM・依存関係脆弱性スキャン）

#### 目的
`docs/plans/ci-quality-gate-hardening.md` が既に導入したsecret scan（gitleaks）に加え、依存関係脆弱性スキャンとSBOM生成をCI/リリースパイプラインへ追加する。

#### 変更対象ファイル

1. **`.github/workflows/ci.yml`**: `backend` ジョブへ依存関係脆弱性スキャンのステップを追加（PRごとに実行し、マージ前に検出する）。

```yaml
      - name: Audit dependencies for known vulnerabilities
        run: python -m pip_audit
```

`pip-audit`（2026-09-20時点PyPI最新2.10.1）は `dev` extraへ追加する。

```toml
dev = [
  # ...既存の行はそのまま...
  "pip-audit>=2.10,<3",
]
```

`pip-audit` はデフォルトで現在の（アクティブな）Python環境にインストール済みの配布物を対象にスキャンする（[pip-audit公式README](https://github.com/pypa/pip-audit#usage)）。CIは直前のステップで `pip install -e ".[dev]"` 済みのため、追加の引数無しでそのまま実行できる。

2. **`.github/workflows/release.yml`**: SBOM生成ステップとGitHub Releaseへの添付を追加。

```yaml
      - name: Generate SBOM
        uses: anchore/sbom-action@v0
        with:
          path: .
          format: spdx-json
          output-file: sbom.spdx.json
      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh release create "${{ github.ref_name }}" dist/* sbom.spdx.json --generate-notes --verify-tag
```

`anchore/sbom-action` はSyftをラップしたGitHub Marketplace Actionで、SPDX JSON形式のSBOMを生成する（[anchore/sbom-action公式README](https://github.com/anchore/sbom-action)）。本計画で新規に信頼する外部Actionであることを明記する。既存のsecret scan（gitleaks）は`docs/plans/ci-quality-gate-hardening.md`の検討時点ではgitleaks-action等の外部Actionも候補だったが、最終的にDocker直接実行（`ci.yml`の`secret-scan`ジョブ）に決定した経緯がある。本Unitでは、SBOM生成をSyft CLIのDocker直接実行ではなく`anchore/sbom-action`（外部Action）で行う判断をしており、既存判断とは異なる点を実装エージェントは認識すること。**導入前にこのActionの最新の固定タグ（`@v0`をコミットSHAやより具体的なバージョンタグへ変更すべきか）を確認すること。**

#### テスト方針
- CI自体の変更のため、ローカルでの単体テストは無い。実装エージェントはブランチをpushしてGitHub Actions上で `ci.yml`（`pip-audit`含む全ジョブ）が成功することを確認する。既知の脆弱性が検出された場合は、この計画の対象外の別タスクとして依存関係の更新を報告する（Stop Conditionには該当しないが、DoDの「Regression」確認に含める）。
- タグpushでの `release.yml` 実行確認は、実際にタグを打つ必要があるため、実装完了時点では「ワークフロー定義のYAML構文とジョブ順序の確認」までとし、実タグでの動作確認は次回リリース時の運用担当者に委ねる（`NOT VERIFIED`として明示する）。

#### 想定リスク
低。既存2ジョブへのステップ追加のみ。`pip-audit`が既存依存の既知脆弱性を検出した場合、CIが失敗するようになる(意図した挙動)。導入直後に失敗する場合は、脆弱性への対応(バージョン更新等)を本Unitとは別に報告すること。

---

### Unit 10: 顧客向け運用ドキュメント

#### 目的
運営者によるSLO保証ではなく、顧客が自分の環境で実行するための手順書を提供する。

#### 新規作成ファイル

**`docs/knowledge/self-hosted-deployment.md`** に、少なくとも次の項目を含める。

1. **初回セットアップ**: `.env`の作成、`DATABASE_URL`、OIDC設定（`OIDC_ISSUER`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET`、外部IdPでのRedirect URI登録方法）、`SESSION_SIGNING_SECRET`の生成方法（`python -c "import secrets; print(secrets.token_urlsafe(32))"`、既存の`MARKET_STREAM_TICKET_SECRET`の案内文と同じ形式に揃える）。
2. **ライセンスの有効化**: 運営者から受け取った `license.json` の配置場所（`LICENSE_FILE_PATH`）、ライセンス無効時に全APIが503を返すこと、更新には再起動が必要なこと（Unit 6の制約）。
3. **バックアップ/リストア手順**: PostgreSQLの `pg_dump`/`pg_restore`、`.secrets`ディレクトリ（`LocalEncryptedSecretStore`使用時）のバックアップ対象への追加、AWS Secrets Manager使用時（Unit 7）はAWS側のバックアップ方針に従う旨。
4. **migration rollback方針**: `alembic downgrade -1` の使用方法と、`docs/plans/ci-quality-gate-hardening.md` のDBマイグレーション安全性チェック（upgrade/downgrade往復試験）が保証する範囲。
5. **障害対応手順の索引**: `docs/knowledge/` 配下の既存トラブルシュート文書（`account-selection-state-fix.md`等）への参照。
6. 冒頭に明記すること: 「本書は運営者によるSLA/SLO保証を意味しない。顧客自身の運用責任の下で実行される（ADR 0005）」。

#### 変更対象ファイル
- `docs/architecture-alignment-and-long-term-roadmap.md`: Horizon 5節の実装・整備項目チェック状態を、本計画のUnit進捗に応じて更新する（実装完了ごとに `[x]` を付す運用は、Horizon 1/2節の既存の書き方に揃える）。

#### テスト方針
テスト対象なし（ドキュメントのみ）。実装エージェントは、手順書に書いたコマンドを実際にローカルで実行し、記載どおりに動作することを確認してから完了とする。

#### 想定リスク
低。

---

## 4. 動作確認手順（共通）

各Unit実装後、次を手動で確認する（自動テストで担保できない結線部分の確認）。

1. `uvicorn app.main:app --reload` でAPIサーバーをローカル起動する（既存READMEの手順に従う）。
2. `curl http://localhost:8000/api/v1/health` が `{"status": "ok", ...}` を返すこと（ライセンス依存を追加しても`/health`は影響を受けないことの確認、Unit 6完了後に必須）。
3. Unit 3完了後: ブラウザで `http://localhost:8000/api/v1/auth/login` を開き、モックまたはテスト用の外部IdP（例: 無料枠のあるAuth0テナント等、実際に契約・設定が必要。実装エージェントが独自に用意できない場合は、この項目を`NOT VERIFIED`として報告し、利用者に実IdPでの確認を依頼する）へリダイレクトされることを確認する。
4. Unit 4完了後: `curl -b cookies.txt -c cookies.txt http://localhost:8000/api/v1/workspaces` を、ログイン前(401)・ログイン後・別ユーザーでの所属ワークスペース差分、の3パターンで確認する。
5. Unit 6完了後: `LICENSE_FILE_PATH` を存在しないパスに設定した状態で任意のAPIを呼び、503が返ることを確認する。次に `scripts/issue_license.py` で有効なライセンスファイルを発行して配置し、再起動後に成功することを確認する。
6. Unit 9完了後: ブランチをpushし、GitHub Actions上で `ci.yml` の全ジョブ（`backend`, `worker-storage`, `secret-scan`, `frontend`)が成功することを確認する（Web UIまたは `gh run list`/`gh run view`で確認する）。

各確認の結果(成功/失敗/NOT VERIFIED)を、AGENTS.md §15の完了報告フォーマット(Changes/Verification/Tests/Documentation/Remaining Issues)に従って報告すること。

---

## 5. Definition of Done

`docs/quality/definition-of-done.md` の全項目に従う。特に本計画に関連して次を強調する。

- Security節: Authentication(Unit 3), Authorization(Unit 4), Secret Management(Unit 2, 7), Injection(該当なし想定だが確認), Information Disclosure(セッションCookie・ライセンスファイル・サービスアカウントキーをログ・エラーメッセージへ出さないこと)。
- Regression節: 「既存の`require_owner`を前提にしていたテストが置き換え後も全て成功する」(Unit 4)、「DB migrationの安全性(upgrade/downgrade往復)」(Unit 5, `docs/plans/ci-quality-gate-hardening.md`の既存チェックがそのまま対象に含める)。
- 実行できなかった検証項目(外部IdPとの実結合、実AWS環境、実タグでのrelease.yml実行)は`NOT VERIFIED`として明示し、理由を書く。未実行項目を成功扱いにしない。
