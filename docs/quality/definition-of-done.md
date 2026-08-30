# Definition of Done

変更は以下の条件を満たした場合のみDoneとする。

## Requirements

- [ ] 実装対象の要件を満たしている
- [ ] 要件外の機能を不必要に追加していない
- [ ] Acceptance Criteriaを満たしている

## Design

- [ ] Architectureに従っている
- [ ] DB設計と矛盾していない
- [ ] API設計と矛盾していない
- [ ] Module/Class責務が適切
- [ ] 不必要な依存関係を追加していない
- [ ] 不必要な技術・抽象化を追加していない

## Code Quality

- [ ] Formatterが成功
- [ ] Lintが成功
- [ ] TypeCheckが成功
- [ ] Buildが成功
- [ ] debug codeが残っていない
- [ ] unrelated changeが含まれていない

## Testing

- [ ] 必要なUnit Testが存在
- [ ] 必要なIntegration Testが存在
- [ ] 必要なE2E Testが存在
- [ ] Bug FixにはRegression Testが存在
- [ ] 既存テストがすべて成功

## UI

UI変更が存在する場合:

- [ ] 実際の画面で確認
- [ ] Designと一致
- [ ] 主要Interactionが動作
- [ ] Error stateが正常
- [ ] Validationが正常
- [ ] Console Errorがない
- [ ] 主要viewportで破綻しない

UI変更が存在しない場合はN/A。

## Security

変更内容に応じて確認:

- [ ] Authentication
- [ ] Authorization
- [ ] Input Validation
- [ ] Secret Management
- [ ] Injection
- [ ] Information Disclosure

該当しないものはN/A。

## Regression

- [ ] 既存機能が壊れていない
- [ ] 変更箇所以外への影響を確認
- [ ] API compatibilityを確認
- [ ] DB migrationの安全性を確認

## Documentation

必要な場合:

- [ ] Architecture更新
- [ ] DB設計更新
- [ ] API設計更新
- [ ] Known Issues更新
- [ ] Project Rules更新

## Final Review

- [ ] `git diff` 全体を確認
- [ ] 不要な変更がない
- [ ] TODO/FIXMEを意図せず残していない
- [ ] Agent Self Reviewを実施
- [ ] 実行していない検証を明示

---

# Completion Rule

以下のいずれかに該当する場合はDoneと報告しない。

- Test failure
- Lint failure
- TypeCheck failure
- Build failure
- 未解決の重大なSecurity issue
- 要件との不一致
- 設計との説明できない乖離

環境上実行不可能な検証がある場合は、

`NOT VERIFIED`

として明示し、理由を報告する。