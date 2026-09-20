# CI品質ゲート強化とmainブランチ保護

状態: 1・3実装済み・main反映済み。2実装済み・ブランチpush済み(PR未作成)。
4はユーザーの判断でE2E実装コストが高いため、機能実装が一通り終わってから
まとめて着手する方針に変更(先送り、計画自体は維持)。

## 背景

現在のci.yml（`backend` / `worker-storage` / `frontend`）はGitHub上のチェック名として
使われているが、mainブランチはこれらの合否に関わらずマージ可能な状態にある。

また、definition-of-done.mdとAGENTS.mdが要求する検証項目のうち、実際にはCIで
カバーされていないものが4点見つかった。

1. frontendのユニットテスト（vitest）がCIで実行されていない
2. E2Eテスト（AGENTS.md 9節が要求）が存在しない
3. DBマイグレーションの安全性チェック（definition-of-done.md「Regression」節）が存在しない
4. シークレットスキャンが存在しない

本計画は、この4点をCIに追加し、その後mainブランチのマージをGitHub Rulesetsで
`backend` / `worker-storage` / `frontend` （＋今回追加するジョブ）の合格必須にする
ための実装順序を定義する。Ruleset自体はGitHub側の管理設定でありコード変更では
ないため、リポジトリ管理者が画面から適用する（本計画のスコープ外、別途手順書を
用意する）。

paper/testnet専用という既存の安全境界には触れない。取引所への実通信経路や
認証方式は変更しない。

---

## 1. frontendユニットテストをCIに追加 `[x]` 実装済み(PR #20, main反映済み)

### 実装目的
既存のvitestテストがCIで一切実行されていない穴を埋め、フロントエンドの回帰を
自動検出できるようにする。

### 変更対象
`.github/workflows/ci.yml` の `frontend` ジョブ。

### 新規作成対象
なし。

### データフロー
変更なし（CIジョブ内でのコマンド追加のみ）。

### 依存関係
`frontend/package.json` の既存 `test` スクリプト（vitest）をそのまま使う。
新規パッケージ追加は不要。

### 実装順序
1. ローカルで `npm run test` を実行し、既存テストが現状で全件成功するか確認する
   （失敗がある場合はこの計画の前提が崩れるため、先に報告する）。
2. `ci.yml` の `frontend` ジョブに `test` ステップを追加する（lintとbuildの間）。
3. PRを作成し、GitHub Actions上で3ジョブすべてが成功することを確認する。

### テスト方法
ローカルでの `npm run test` 実行結果、およびPR上のGitHub Actions実行結果。

### 想定リスク
低。既存テストが現状ローカルで失敗している場合のみ、スコープが「CI追加」から
「既存テスト修正」に広がる。その場合は先に報告し、別スライスとして扱う。

---

## 2. DBマイグレーション安全性チェック `[x]` 実装済み(ブランチ`ci/db-migration-safety-check`にpush済み、PR未作成・CI未確認)

### 実装目的
definition-of-done.mdの「DB migrationの安全性を確認」を自動化し、
upgrade/downgradeが破綻するマイグレーションをPR時点で検出する。

### 変更対象
`.github/workflows/ci.yml`（新規ジョブ、または `worker-storage` ジョブへのステップ追加）。

### 新規作成対象
使い捨てPostgresに対して `alembic upgrade head` → `alembic downgrade base`
（または1段階ずつ）を実行し、エラーなく往復できることを確認するステップ。

### データフロー
CIランナー内の使い捨てPostgresコンテナに対してのみ実行する。運用DB・本番相当の
データには一切触れない。

### 依存関係
`worker-storage` ジョブと同じ `postgres:16` サービスパターンを流用できる見込み。

### 実装順序
1. 既存マイグレーション履歴を確認し、`downgrade()` が未実装（`pass`のみ等）の
   リビジョンがないか棚卸しする。ある場合はこのチェックの設計（全downgrade往復
   か、upgradeのみの検証か）を先に決める必要があるため、その時点で選択肢を報告する。
2. ローカルで使い捨てDBに対しupgrade/downgradeが成功することを確認する。
3. CIジョブ（またはステップ）として追加する。

### テスト方法
CI上でのupgrade/downgrade実行結果。

### 想定リスク
中。既存マイグレーションにdowngrade未実装のものがあった場合、チェック方式の
設計変更が必要になる可能性がある（AGENTS.md 13節のStop Conditionsに該当する
可能性があるため、その場合は推測で進めず報告する）。

---

## 3. シークレットスキャン導入 `[x]` 実装済み(PR #22, 誤検知なし)

### 実装目的
`.env` や認証情報の誤コミットを機械的に検出する（tech-stackの「credential/secretを
露出させない」方針への対応）。

### 変更対象
`.github/workflows/ci.yml`（新規ジョブ）。

### 新規作成対象
スキャンツール（gitleaks等）の設定ファイルと、専用CIジョブ。

### データフロー
変更なし（リポジトリの差分・履歴をスキャンするのみ）。

### 依存関係
外部GitHub Action（gitleaks-action等）を新規導入するか、pip/npm経由のツールを
使うかの選定が必要。有料サービスの導入ではないが、外部Actionを新たに信頼する
判断になるため、選定案を先に提示する。

### 実装順序
1. ツール候補を比較し選定案を提示する（無料・メンテナンス状況・誤検知率）。
2. 既存リポジトリ全体を一度スキャンし、誤検知（サンプル値・テスト用ダミーキー等）を
   洗い出し、allowlist設定を用意する。
3. PR差分に対するスキャンをCIジョブとして追加する。

### テスト方法
ローカルまたはCI上でのスキャン結果。既知の非シークレット文字列が正しく
除外されることを確認する。

### 想定リスク
低〜中。テストコード内のダミーAPIキー等で誤検知が出やすく、allowlist調整に
やや手間がかかる可能性がある。

---

## 4. E2Eテスト（Playwright）導入 `[-]` 先送り(実装コスト・工数が大きいため、機能を一通り実装してからまとめて着手する方針)

### 実装目的
AGENTS.md 9節が求めるUI確認（レイアウト・Interaction・Error state・Console error等）を
自動化し、UI回帰を機械的に検出できるようにする。

### 変更対象
なし（新規追加）。

### 新規作成対象
- `playwright.config.ts`
- `tests/e2e/` 配下の初期スモークテスト（主要4画面：development/connections/
  OANDA market/Binance market の表示確認から開始）
- `ci.yml` への `e2e` ジョブ追加

### データフロー
ブラウザ → frontend dev server → backend API → CI内の使い捨てPostgres、という経路。
OANDA Practice / Binance Spot Testnetへの実通信は行わず、モック化するか
スコープ外にするかを実装開始前に決める（実testnet呼び出しをCIに含めると
外部サービス依存でフレーキーになりやすいため、最初はモック方針を推奨する）。

### 依存関係
`@playwright/test` の追加、CI上でのブラウザインストール、バックエンド起動に
`worker-storage` と同様の使い捨てPostgresが必要。

### 実装順序
1. ローカルでPlaywrightが動く最小構成を作る。
2. 主要4画面のスモークテスト（表示確認レベル）を1画面ずつ追加する。
3. CIジョブとして組み込み、安定して成功することを確認する（フレーキーな場合は
   原因を調査してから追加を進める）。

### テスト方法
ローカルでのPlaywright実行、CI実行結果。

### 想定リスク
中〜高。4項目の中で最も設計・調整コストが高い。CI実行時間の増加と、
外部依存（実testnetか、モックか）の設計判断が必要。4項目の中で最後に着手する。

---

## 全体の実装順序

依存の薄さとリスクの低さから、以下の順で進めるのが妥当と考える。

1. frontendユニットテストのCI追加（最小リスク、即着手可能）
2. シークレットスキャン導入（既存コードに影響しない）
3. DBマイグレーション安全性チェック（`worker-storage` パターンを流用）
4. E2Eテスト導入（設計コストが最も高いため最後）

各項目とも、着手前に対象スライスの開始条件・完了条件を確認してから進める。

## Ruleset適用（本計画のスコープ外・別途）

上記4項目のCI追加が完了し、GitHub Actions上で安定して成功することを確認した後、
`Settings → Rules → Rulesets` で `main` を対象に以下を設定する（画面操作、
リポジトリ管理者が実施）。

- Require a pull request before merging
- Require status checks to pass before merging：
  `backend` / `worker-storage` / `frontend` （＋今回追加するジョブ名）
- Require branches to be up to date before merging
- Bypass listは空にする
