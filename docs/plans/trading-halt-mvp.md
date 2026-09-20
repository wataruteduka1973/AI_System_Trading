# trading_halt発動・解除ロジック 実装計画(MVP)

対象ADR: `docs/decisions/0004-trading-halt-mvp-scope.md`

## 実装目的

Risk Gateが既に検知している2原因(データ遅延、日次/週次損失・最大DD上限)について、
個別注文への`deny`だけでなく、永続的な`trading_halt`状態への遷移を実装する。
`level`列(warning/entry_halted/all_trading_halted/emergency_stopped)の状態機械を
05番「停止レベルの状態遷移表」に従って実装し、`status`列は`active`/`released`の
2値のみ使用する(ADR 0004)。

## 変更対象

- `app/trading/application/risk_gate.py`: data_delay/daily_loss/weekly_loss/
  peak_drawdownのhard_breach検知結果を使って、halt発動/エスカレーション/
  デエスカレーションを呼び出す。モジュールdocstringの「trading_halt活性化は
  このモジュールのスコープ外」という記述を、本MVPの2原因については訂正する
- `app/trading/application/order_flow.py`: `place_order`にtrading_haltチェックを
  追加する(`TODO(trading_halt)`の解消。該当2原因由来のhaltのみ、残り8原因は
  引き続きチェック対象外と明記する)

## 新規作成対象

- `app/trading/application/trading_halt.py`: `level`列の状態機械(発動・
  エスカレーション・デエスカレーション・emergency_stopped Owner解除)を
  DB操作込みで実装。「1つの原因につき`trading_halt`行は1つ」の原則を守るため、
  `(workspace_id, scope_type, scope_id, reason_code)`で既存active行を検索する
  ヘルパーを含む
- `app/trading/application/dummy_pipeline.py`または呼び出し元: 新規建玉評価前に
  有効なhalt(`entry_halted`以上)をチェックする箇所を追加(具体的な接続点は
  実装時に既存コードを読んで決定)

## データフロー

1. `risk_gate.evaluate_signal`が既存通りdata_delay/daily_loss/weekly_loss/
   peak_drawdownを評価する(既存ロジック、変更なし)
2. hard_breachが発生した場合、`trading_halt.activate_or_escalate`を呼び、該当
   `reason_code`のactive行が無ければ新規発効(`entry_halted`)、既存行があれば
   現在の`level`より重い場合のみエスカレーション
3. 該当チェックがpassした場合(閾値から回復)、`trading_halt.deescalate_one_step`
   を呼び、該当`reason_code`のactive行があれば1段階だけ緩和する
   (entry_halted→warning、all_trading_halted→entry_halted等。emergency_stoppedは
   対象外)。`warning`から緩和条件を満たした場合は`status=released`・
   `released_at`記録まで行う
4. `order_flow.place_order`は新規建玉注文の実行前に、該当スコープの
   `entry_halted`以上のactive haltが無いことを確認する(決済注文は許可、
   マトリクスの「決済: 許可」列に従う)

## 依存関係

- 既存の`RiskDecision`/`RiskState`(Unit 2で純粋化済み)を変更しない
- `TradingHalt`ORMモデル(既存、`app/models/strategy.py`)をそのまま使用

## 実装順序

### Unit A: trading_halt.pyの状態機械本体
- `activate_or_escalate`、`deescalate_one_step`、`release_emergency_stop`を実装
- テスト: 新規発効、エスカレーション、段階的デエスカレーション、`emergency_stopped`
  は直接解除される例外、既存activeがある場合は同一行のlevelのみ書き換え(新規行を
  作らない)ことを確認

### Unit B: risk_gate.pyとの接続
- data_delay/daily_loss/weekly_loss/peak_drawdownのhard_breach/pass結果から
  `trading_halt`を呼び出す
- テスト: 閾値超過でhalt発効、閾値回復で緩和されることを確認(既存のrisk_gate
  テストは無変更のまま成功することもあわせて確認)

### Unit C: order_flow.pyでのhaltチェック
- `place_order`に、新規建玉(dote-gatingの反対決済ではない注文)実行前の
  haltチェックを追加
- テスト: `entry_halted`中は新規建玉が拒否され、決済注文は通ることを確認

## テスト方法

- 各Unitごとにunit test必須(pytest、既存の慣習通りモックDB)
- 既存のrisk_gate/order_flow関連テストは全てregressionとして再実行し成功を確認

## 想定リスク

- `trading_halt`のスコープ(bot vs account)を誤ると、意図しない範囲まで新規建玉を
  止めてしまう可能性がある。data_delayは`scope_type=bot`、日次/週次損失・DDは
  `scope_type=account`とする(マトリクスの「該当Bot/銘柄」「口座またはBot」の
  記述に基づく判断、実装時に確定)
- デエスカレーションの「十分離れたことを再確認」という要件を、単純な「今回の
  チェックがpassした」だけで満たしたとみなすのは簡略化である(本来は連続して
  複数回passすることを求める設計もありうる)。MVPでは1回passで緩和する単純な
  実装とし、ヒステリシス不足による頻繁な発動/解除の往復が問題になれば別途対応する

## Definition of Done

`docs/quality/definition-of-done.md`に従う。
