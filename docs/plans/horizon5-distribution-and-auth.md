# Horizon 5 配布運用・認証基盤 実装計画(ドラフト)

対象ADR: `docs/decisions/0005-horizon5-self-hosted-distribution.md`

**本計画はドラフトである。** Round1 Q2(グリル質疑)で確認した通り、開始条件は
本ADRにより充足したが、実装着手そのものはHorizon4-lite(Backtest)完了後に改めて
利用者承認を得てから行う。本ドキュメント単体を実装着手の許可として扱わない。

本計画は、AGENTS.md §4の要求項目(実装目的・変更対象・新規作成対象・データフロー・
依存関係・実装順序・テスト方法・想定リスク)に沿って、Horizon5の仕様を実装着手できる
粒度まで言語化したものである。

## 実装目的

roadmap Horizon5の完了条件(認証・RBAC・Secret管理・監査・通知・リリースゲート)を
満たす。ただしADR 0005により配布モデルがセルフホスト型ソフトウェア販売に一本化された
ため、roadmap原文が前提としていた「運営者が本番環境を運用する」性質の項目
(Secret Manager運用化、環境分離、backup/restore、SLO)は、**「顧客が自分の環境で
運用するための機能提供・ドキュメント」**に再定義する。運営者自身が24/365で稼働保証する
対象は存在しない。

## 事前調査で判明した設計上の論点

1. **`user_membership`テーブルはDBスキーマに既存**(`database/postgresql_schema_v0.1.sql`
   46-52行目、`role IN ('owner', 'operator', 'viewer')`、`(workspace_id, user_id)`単位)。
   ただしORMモデル(`UserMembership`クラス)は未実装で、`app/models/workspace.py`には
   `Workspace`と`AppUser`のみ存在する。現在の認証(`app/security/auth.py`)はこの仕組みを
   一切使わず、単一固定トークン(`x-owner-token`一致確認のみ)で動いている。
   → RBAC基盤はゼロ設計ではなく、**既存スキーマへ実際の認可チェックを接続する作業**が中心。
2. **`system_worker`ロールは`user_membership`のCHECK制約に含まれていない**
   (owner/operator/viewerのみ)。roadmapが挙げる4ロールの一角だが、人間の
   ワークスペースメンバーシップとは性質が異なる(バックグラウンドワーカーのサービス
   アイデンティティ)ため、別テーブル(サービスアカウント/APIキー)として新設する
   方針とする(本計画Unit 4)。
3. **Secret管理の抽象化が未整備**: `app/services/secrets.py`の`LocalEncryptedSecretStore`は
   具象クラスのみで、インターフェース(Protocol/ABC)が存在しない。プラガブル化には
   まずこの抽象化を導入する必要がある。
4. **`.github/workflows/release.yml`が既にタグpush起点でPythonパッケージをビルドし
   GitHub Releasesへ公開している**。セルフホスト配布の土台として使えるが、SBOM生成・
   依存関係脆弱性スキャン・secret scanは未追加(`.github/dependabot.yml`のみ存在)。

## スコープ外(本ドラフトでは扱わない)

- マネージドホスティング(ADR 0005により当面提供しない)
- 有償サポート契約・SLA(グリルRound4 Q5で「ソフトウェア+ドキュメント提供のみ」と決定)
- Gmail API等、汎用SMTP以外の通知アダプタの実装(インターフェースは用意するが、
  具体アダプタの追加は別タスク)
- ライセンス販売形態(買い切り/サブスク)の最終決定(グリルRound4 Q2で未定のまま。
  本計画のライセンス機構はどちらでも使える設計とする)

## 変更対象

- `app/security/auth.py`: 固定トークン認証を廃止し、OIDC Authorization Code + PKCE
  ベースの認証 + RBAC認可チェックへ置き換える
- `app/models/workspace.py`: `UserMembership` ORMモデルを追加し、`Workspace`/`AppUser`
  との relationship を配線する
- 既存の全APIルート: `require_owner`依存を、`workspace_id`+`role`を見る認可依存へ
  置き換える(Owner/Operator/Viewerで許可範囲を分岐。影響範囲が広いため実装順序は
  段階移行とする)
- `app/services/secrets.py`: `LocalEncryptedSecretStore`が満たすべきインターフェース
  (Protocol)を切り出す(既存の`get_secret_store()`呼び出し元は変更不要な形にする)

## 新規作成対象

- `app/security/oidc.py`(仮称): OIDCクライアント実装(Authorization Code + PKCE)。
  IdP設定はプラガブル(自己完結型内蔵IdP、または外部IdPのdiscovery endpoint設定)
- `app/security/rbac.py`(仮称): `workspace_id`+`role`の認可チェックヘルパー、
  FastAPI dependency
- `app/security/service_account.py`(仮称)+ 対応migration: System Worker用の
  サービスアカウント/APIキー発行・検証の仕組み(`user_membership`とは別テーブル)
- `app/licensing/`(新規パッケージ、仮称): オフライン検証ライセンスキー機構
  (署名済みライセンスファイルの検証、公開鍵埋め込み、有効期限の有無で買い切り/
  サブスク両対応。運営者は常時稼働のアクティベーションサーバーを持たない)
- `app/notifications/`(新規パッケージ、仮称): 汎用SMTPアダプタ + Outbox/Event Log
  連携。将来のGmail API等はこのアダプタインターフェースへの追加として拡張する
- `app/services/secrets_backends/`(仮称): AWS Secrets Manager/GCP Secret Manager/
  Vault等、追加のSecretStore実装(既存の`LocalEncryptedSecretStore`はデフォルトとして残す)
- `docs/knowledge/self-hosted-deployment.md`(仮称): 顧客向けのバックアップ/
  リストア/migration rollback手順書(運営者のSLO保証ではなく手順提供であることを明記)
- `.github/workflows/`への追加ステップ: SBOM生成(例: syft)、依存関係脆弱性スキャン
  (例: pip-audit)、secret scan(例: gitleaks)をrelease gateへ組み込む

## データフロー

1. 顧客側の利用者がログイン要求 → 自己完結型IdPまたは外部IdPへリダイレクト
   (Authorization Code + PKCE) → コールバックで`id_token`検証 → `oidc_subject`で
   `AppUser`を解決(初回は`invited`→`active`遷移)
2. 各APIリクエストは解決された`AppUser`と対象`workspace_id`に対する
   `user_membership.role`を引き、role別の許可アクションかどうかを判定してから
   既存Application Use Caseを呼ぶ
3. ソフトウェア起動時にライセンスファイルを読み込み、公開鍵で署名検証・
   (存在する場合)有効期限チェックを行う。検証失敗時の挙動(起動拒否 or 機能制限
   モード)は実装着手時に確定する
4. Application層でドメインイベントが発生した際、Event Log/Outboxへ書き込み →
   Notification Workerが未送信レコードをポーリングし、設定された通知アダプタ
   (デフォルトSMTP)経由で送信、送信結果をOutboxへ記録(重複防止・再送制御)

## 依存関係

- `docs/decisions/0005-horizon5-self-hosted-distribution.md`(配布モデル決定)
- 既存DBスキーマの`user_membership`テーブル(v0.1から存在、ORM未実装)
- 既存の`app/services/secrets.py`(`LocalEncryptedSecretStore`)
- Horizon4-lite(Backtest)の完了(グリルRound2 Q5により、本格着手はその後)

## 実装順序(草案、確定ではない。着手時に利用者承認を得ること)

### Unit 1: `UserMembership` ORM + 既存`Workspace`/`AppUser`とのrelationship配線
- 既存DBスキーマへの接続のみ、migration不要(テーブル自体は既存)

### Unit 2: `LocalEncryptedSecretStore`のインターフェース抽出
- 既存呼び出し元の挙動は変更しない、regression防止

### Unit 3: OIDCクライアント実装(まず外部IdP接続のみ、Authorization Code+PKCE)

### Unit 4: RBAC認可dependencyを既存APIへ段階的に適用(`require_owner`の置き換え)
- 影響範囲が広いため、モジュール単位で分割して移行する

### Unit 5: System Worker用サービスアカウント機構(新規migration)

### Unit 6: オフライン検証ライセンスキー機構

### Unit 7: Secret Managerプラガブルバックエンド追加(AWS/GCP/Vault)

### Unit 8: 通知(汎用SMTP)+ Outbox/Event Log

### Unit 9: リリースゲート(SBOM/脆弱性スキャン/secret scan)追加

### Unit 10: 顧客向け運用ドキュメント(バックアップ/リストア/障害対応手順)

## テスト方法

- 各Unitごとにunit test必須
- RBAC: role別の許可/拒否のマトリクステスト(Owner/Operator/Viewerそれぞれで
  許可されるべきAPI・拒否されるべきAPIを網羅)
- OIDC: Authorization Code+PKCEフローの結合テスト(モックIdP使用)
- ライセンス検証: 正常署名/改竄された署名/期限切れ/期限なしの各ケース
- 既存の`require_owner`依存を使うテストは、置き換え後も全て成功することを
  regressionとして確認する

## 想定リスク

- 全APIの`require_owner`置き換えは影響範囲が広く、regressionリスクが高い。
  Unit単位で段階的に移行し、既存テストを都度確認する
- Binance側ToSの残存リスク(ADR 0005「残存リスク」節参照)。公開ベータ・有償販売
  開始前に再確認が必要
- ライセンスキーのオフライン検証は、署名鍵の管理(運営者側)がそのままセキュリティ上
  の要となる。鍵管理方法(保管場所、ローテーション方針)は実装着手時に別途設計する
- System Workerロールは新規テーブル追加(migration)が必要。既存の`user_membership`
  CHECK制約(owner/operator/viewerのみ)を変更するか別テーブルにするかの判断は
  Unit 5着手時に既存コードを再確認して決定する

## Definition of Done

`docs/quality/definition-of-done.md`に従う。ただし本ドキュメントはドラフトであり、
実装着手そのものにはHorizon4-lite完了後に改めて利用者承認を得ること。
