# 注文・台帳の照合と、不整合での口座の停止

状態: 実装済み(2026-10-07)。FR-ORD-06、取引停止マトリクス「注文・台帳不整合」(該当口座 / `emergency_stopped` / 解除は「照合完了＋Owner承認」)。

## 何を照合するか

ペーパーでは内部の台帳が正本で(要件 §235)、突き合わせる取引所は無い。起こりうるのは、内部の記録どうしが食い違うこと。
1回の約定は `place_order` が1トランザクションで、約定・注文・台帳・建玉の4か所に書く。バグ・手作業の修正・一部だけの復元などで、これが崩れうる。
`ledger_reconciliation.reconcile` は、その不変条件を口座ごとに確かめる(行を渡すと結果を返す純粋関数で、DB なしでテストできる)。

| 検知コード | 内容 |
|---|---|
| `fill_without_ledger` | 約定に台帳の取引(`reference_type='fill'`)が無い |
| `ledger_cash_mismatch` / `ledger_fee_mismatch` | 台帳の現金が ∓価格×数量、手数料が -fee_amount と合わない |
| `ledger_entry_without_fill` / `ledger_transaction_without_fill` | 約定に結びつかない台帳の記録、約定の無い台帳の取引 |
| `order_filled_quantity_mismatch` / `order_filled_incompletely` | 注文の約定数量が約定の合計と違う、全部約定なのに数量が足りない |
| `order_status_not_in_history` | 注文の状態が、状態の履歴に一度も出ていない(履歴が無い古い注文は見ない) |
| `multiple_open_positions` / `open_position_without_*` / `closed_position_with_quantity` | 同じ銘柄に開いた建玉が2つ以上、数量や平均建値が無い、閉じた建玉に数量が残っている |
| `position_quantity_mismatch` | 開いた建玉の符号つき数量が、約定の売買の差し引きと違う |
| `realized_pnl_mismatch` | 建玉行の `realized_pnl` の合計が、台帳の `realized_pnl` と違う |
| `position_cost_basis_mismatch` | 約定の現金の合計 + 建玉の符号つき取得原価 = 実現損益、が成り立たない(平均建値を、平均の計算をやり直さずに約定とつなぐ) |

金額は 1e-9 の許容差で比べる(台帳は保存時に18桁へ丸める)。1口座の報告は50件まで。

## 見ないもの(限界)

- 約定と台帳が**同じ価格から同じ向きに**間違っている場合(どちらも約定の価格から作られる)。
- 入金・`adjustment`(約定に結びつかない記録)、`account_snapshot`(その時点の観測で、後から再計算できない)。
- 取引所との照合。ペーパーには無い。実取引(別プロジェクト)では、`unknown` 状態の注文の復旧とあわせて必要になる。

## 発動・通知・解除

- `ledger_check.check_ledger_reconciliation` を**通知 Worker が5分ごと**に実行する(約定を書く Trading Worker とは別のプロセスなので、チェック自体が書き込み側と同じ故障を共有しない)。
  約定が1件でもある口座を口座ごとに1トランザクションで調べ、1つの口座の失敗は他を止めない。
- 不整合があれば、口座スコープの `emergency_stopped`(`reason_code=ledger_mismatch`、自動解除なし)を発動する。新規・増し玉は `place_order` が拒否し、
  Owner と Operator に通知が届く(`SystemEvent` の payload に検知コードと対象の ID、最大10件)。システム状態 API の `problems` にも出る。
  すでにこの原因で停止中の口座は、再度は調べない(解除のときに調べ直す)。
- 解除は Owner の `/emergency-release`。**この原因に限り、解除の前に照合をやり直し、まだ不整合があれば 409** で断る(「照合完了＋Owner承認」)。
  他の `emergency_stopped`(利用者の緊急停止)は従来どおり。

## 決めたこと・判断

- **決済は止めない**: マトリクスは「決済: 手動手順のみ」だが、`place_order` は、どの `emergency_stopped` でも決済を通す(利用者の緊急停止と同じ扱い)。
  台帳を信用できない間の決済を止めるには、`place_order` のゲートを足す必要があり、Bot の評価が「止まっている」と失敗を重ねる副作用もある。ペーパーでは実害が小さいので、今回は入れていない。
- **自動修復はしない**: 台帳は追記のみで、直すには原因の調査が要る。自動で直すと原因が隠れる。直し方は、運用者が DB で行う(調整エントリの追加など)。
  解除の照合が、直ったことの確認になる。
- Bot は止めない。新規が止まるだけで、Bot の状態は変えない(決済のシグナルは通る)。

## 検証

- 単体: `test_ledger_reconciliation.py`(各検知、許容差、銘柄ごと、上限)、`test_ledger_check.py`(発動・通知・失敗の分離・解除の関門)。
- 実 PostgreSQL: `test_ledger_reconciliation_postgres.py` は、**本物の `place_order`**(買い・増し玉・一部決済・ドテン・ショートの追加と決済)でできた記録が不整合0件になること、
  7種類の破損(台帳の金額・取引の欠落・建玉の数量・平均建値・約定数量・状態の履歴)が見つかること、発動が1回で通知が1件であることを確かめる。
- ローカル DB(全部 rollback): 実データの口座13件はすべて不整合0件。実口座の建玉を壊すと検知して緊急停止し、通知が1件配信され、システム状態が `attention` になり、
  解除は 409、直すと解除できた。

## 既知の制限

- 検知は最大5分遅れる(その間の約定は、壊れた台帳の上に積まれる)。
- 多銘柄・多通貨の口座の資産(`account_valuation`)は照合の対象外。
- `ledger_mismatch` の halt は口座単位。口座の Bot 全体が新規を打てなくなる。
