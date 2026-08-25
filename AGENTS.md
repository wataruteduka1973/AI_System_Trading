# AGENTS.md

## 1. Purpose

このリポジトリでは、AI Agent が実装・検証・修正を可能な限り自律的に完結させる。

人間による全コードの逐次レビューを前提としない。

そのため、コード品質は以下によって担保する。

- 設計の事前明文化
- 小さい実装単位
- 静的解析
- 型検査
- 自動テスト
- E2E / UI確認
- Agent自身によるレビュー
- プロジェクト固有知識のドキュメント化

「動いた」だけでは完了ではない。

---

## 2. Source of Truth

実装前に、関連するドキュメントを確認すること。

優先順位は以下。

1. 要件定義
2. Architecture Decision / Architecture Document
3. DB / API / Module等の詳細設計
4. Implementation Plan
5. AGENTS.md
6. 既存コード

推奨構成:

```text
docs/
├── requirements/
├── architecture/
├── design/
│   ├── database/
│   ├── api/
│   └── modules/
├── plans/
├── quality/
└── knowledge/
```

設計と実装が矛盾している場合、独断でどちらかに合わせない。

原因を調査し、

- 何が矛盾しているか
- どちらを変更すべきか
- その理由
- 影響範囲

を提示すること。

---

## 3. Before Implementation

コードを書く前に、必ず既存実装を調査する。

最低限確認すること:

- 関連する既存コード
- 類似実装
- ディレクトリ構造
- アーキテクチャ
- 命名規則
- 使用ライブラリ
- テスト方法
- プロジェクト固有ルール

既存の仕組みで実現可能なら、新しい仕組みを追加しない。

---

## 4. Implementation Plan

一定規模以上の変更では、実装前に計画を作成する。

計画には最低限以下を含める。

- 実装目的
- 変更対象
- 新規作成対象
- データフロー
- 依存関係
- 実装順序
- テスト方法
- 想定リスク

実装単位は可能な限り小さくする。

大きな機能を一度に実装しない。

---

## 5. Architecture Rules

既存アーキテクチャを尊重する。

以下を避ける。

- 責務の混在
- 不必要な抽象化
- 不必要な共通化
- 巨大なクラス / モジュール
- 循環依存
- レイヤー違反
- 既存機能と重複する実装
- 要件に存在しない将来予測による過剰設計

設計を説明できない状態で実装を開始しない。

---

## 6. Coding Rules

プロジェクト既存のCoding Conventionを優先する。

原則:

- 型安全性を維持する
- 不要な `any` / unsafe cast を避ける
- エラーを握り潰さない
- lint/typecheckを無効化して問題を隠さない
- 意味のないコメントを書かない
- public APIの変更は影響範囲を確認する
- unrelated changeを混ぜない

---

## 7. Testing

実装コードとテストコードはセットとして扱う。

変更内容に応じて以下を追加・更新する。

- Unit Test
- Integration Test
- E2E Test
- Regression Test

バグ修正では可能な限り、

1. バグを再現するテスト
2. 修正
3. テスト成功

の順で行う。

テストを通すために仕様を歪めない。

---

## 8. Static Verification

静的に検出可能な問題を人間のレビューに流さない。

変更完了後、プロジェクトで利用可能な以下を実行する。

- Formatter
- Linter
- Type Checker
- Unit Test
- Integration Test
- Build
- E2E Test

コマンドは `package.json`、Makefile、CI設定等から確認すること。

存在しないコマンドを推測して成功したことにしない。

---

## 9. UI Verification

UI変更では、コードだけを見て正しいと判断しない。

利用可能であればブラウザを操作し、

- レイアウト
- 表示内容
- Interaction
- Validation
- Error state
- Responsive behavior
- Console error

を確認する。

Figma・仕様画像等が存在する場合は比較する。

---

## 10. Self Review

実装完了後、自分の変更をレビュアーとして再確認する。

最低限以下を見る。

### Architecture

- 責務分割は適切か
- 既存設計に沿っているか
- より単純な方法はないか

### Correctness

- 要件を満たしているか
- edge caseを考慮しているか
- エラーハンドリングは適切か

### Regression

- 既存機能を壊していないか
- public interfaceへの影響はないか

### Security

- 認証
- 認可
- 入力検証
- secret
- injection
- information disclosure

等の問題がないか。

### Maintainability

- 不必要に複雑ではないか
- 重複していないか
- 命名から意図を理解できるか

---

## 11. Documentation

コードだけでは将来のAgentが判断できない知識はドキュメント化する。

特に以下を残す。

- プロジェクト固有仕様
- 環境依存問題
- 過去に発生した重要なバグ
- 非自明な設計判断
- ライブラリ固有の罠
- 意図的な制約

単なる作業履歴は残さない。

「次のAgentが同じ間違いを避けられる情報」を残す。

---

## 12. Bug Fix Policy

バグ修正では症状だけを直さない。

以下を実施する。

1. 再現
2. Root Cause特定
3. 影響範囲調査
4. Regression Test追加
5. Root Cause修正
6. 関連テスト実行
7. 同種問題の検索
8. 静的検出可能か検討
9. 必要ならドキュメント更新

同じ種類の問題を人間が再度レビューする必要がない状態を目指す。

---

## 13. Stop Conditions

以下の場合、推測で進めず停止して報告する。

- 要件が重大な点で曖昧
- 設計書間に重大な矛盾がある
- destructive operationが必要
- migrationでデータ損失の可能性がある
- security architectureを変更する
- public APIにbreaking changeが必要
- 新しい有料サービス/インフラ導入が必要
- 要件を満たすため設計変更が必要

報告には、

- 問題
- 原因
- 選択肢
- 推奨案
- 影響

を含める。

---

## 14. Definition of Done

「実装した」だけではDoneではない。

`docs/quality/definition-of-done.md` を満たした場合のみ完了とする。

実行できなかった検証項目がある場合、それを明示する。

未実行項目を成功扱いしない。

---

## 15. Completion Report

作業完了時は簡潔に以下を報告する。

### Changes

何を変更したか。

### Verification

何を実行し、成功したか。

### Tests

追加・変更したテスト。

### Documentation

更新したドキュメント。

### Remaining Issues

未解決事項、未実行検証、リスク。

問題がなければ `None` とする。