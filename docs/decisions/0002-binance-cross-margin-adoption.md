# ADR 0002: Binance Cross Marginへの対応方針(調査・設計のみ、実装は別タスク)

- 状態: **保留(Deferred)**(2026-09-20、利用者判断。詳細は「2026-09-20保留の記録」参照)
- 日付: 2026-09-20
- 決定者: 利用者
- 記録者: Claude(このリポジトリのAIエージェント)

本書はコード変更・migration作成を一切伴わない。調査結果と設計方針の記録のみ。

## 2026-09-20 保留の記録

本ADRが§「要・利用者承認事項」で明記した通り、Binance Margin APIには専用の
Testnetが公式に存在せず、Cross Margin対応の動作確認は本番API(実資金が
動きうる環境)に対して行わざるを得ない。この制約について利用者の判断を
仰いだ結果、以下の方針が決定した。

**「厳格にpaper/testnet限定・本番取引/実資金は一切扱わない」という本プロジェクトの
既存原則を、ショート対応の実現よりも優先する。** これにより、

- Binance Cross Margin対応は**今回実装しない**(将来、実際にLive実装
  (本番取引そのもの)へ進む段階で、その一部として改めて着手を検討する)。
- Binance側は当面`conservative-v1`の通りlong-onlyのまま据え置く。
- ショート対応が必要な範囲は、Practice環境で通常の売買として検証可能な
  OANDA側に限定し、別途実装する(OANDAは本番環境問題を生じない)。

本ADRの§1〜4(調査結果・設計案・要承認事項)自体は、上記判断の根拠と
なった記録として、内容を変更せず保持する。将来Binanceが公式Margin Testnetを
提供した場合、または実際にLive実装へ進む段階になった場合に、本ADRを起点として
再評価する(下記「将来の見直し条件」参照)。

## 背景

Binance側でもショートポジションに対応するため、Binance Spot Testnet接続から
Binance Margin APIへの切り替えを検討している。現在の`app/trading/application/order_flow.py`
はBinance/OANDA双方についてlong-onlyのみを扱う実装になっており(2026-09-20実装、
理由はBinance spotがそもそも空売り不可であること、および`08_取引アルゴリズムと
リスク初期値.md`のconservative-v1が「レバレッジ・借入・空売り: 禁止」としている
ことによる)、Binance側でショートを可能にするにはMargin API(信用取引)への
切り替えが必須という前提がある。

前回調査(2026-09-20、本ADRに先行するBinance Margin API実現可能性調査)で、以下が
判明している。

- **Binance公式はMargin専用のTestnetを提供していない**。Spot Testnet
  (`testnet.binance.vision`)は`/api/*`のみ対応で、Margin系(`/sapi/*`)は開発者
  フォーラムでBinanceスタッフが複数回「testnetでは非対応」と明言している。
  サードパーティのクライアントライブラリがMargin Testnet対応を謳う記述もあるが、
  公式見解と矛盾しており真偽未確認。
- Cross MarginとIsolated Marginはエンドポイント体系そのものが別物
  (`/sapi/v1/margin/account` vs `/sapi/v1/margin/isolated/account`等)。
- リスク管理用の閾値(`tradeCoeff`の`marginCallBar`/`forceLiquidationBar`等)や
  `marginLevel`との数値的な対応関係は、Binance公式ドキュメント上に明記されていない。

## 決定内容

1. **Cross Marginを採用する。Isolated Marginは今回の対象外とし、将来の拡張として
   先送りする。**
2. margin健全性を取引所非依存に扱うため、`margin_health_ratio`という正規化フィールド
   (0.0=強制決済相当、1.0以上=安全)を導入する(設計は本書§2)。
3. 新規テーブル`margin_loan`(借入元本の負債管理)を追加する設計とする(本書§3)。
   当初依頼にあった`account_valuation_snapshot`は**新設せず**、既存の
   `account_snapshot`テーブル(2026-08-16の初回migrationで作成済みだが、現時点で
   ORMマッピングが存在しない)を拡張する方針に変更する(理由は本書§3「代替案」参照)。
4. `exchange_connection`/`external_account`のスキーマは、Cross Margin対応の
   最小限追加であれば小規模な変更で済む(本書§4)。

### 要・利用者承認事項(独断で「問題なし」と結論しない)

**Margin専用Testnetが存在しないため、Cross Margin API統合の動作確認は本番の
Binance Margin API(実資金が動きうる環境)に対して行う必要がある。** これは本
プロジェクトの既存原則「paper/testnet限定」(`app/exchanges/binance.py`の
`BinanceSpotTestnetClient`、`05_アーキテクチャと移行計画.md`「`paper`口座から
外部注文を送信してはならない」)と真正面から緊張関係にある。

提案する安全対策(いずれも**未承認・利用者の最終判断が必要**):

- 検証は最小金額(取引所のmin_notional付近)かつ利用者本人の少額実資金でのみ行う
- 検証範囲を「認証・残高照会・tradeCoeff取得」等の**読み取り専用API**に限定した
  第一段階と、実際の借入・発注・返済を伴う**書き込み系API**の第二段階に分け、
  第二段階は利用者の実施タイミング事前承認を別途必須とする
- 書き込み系API検証を行う場合、検証直後に建玉・借入を即座に解消する手順を
  事前に文書化してから着手する
- 本番API検証の実施日時・金額上限を、着手前に利用者へ個別に提示し明示的な承認を得る

**本ADRはこの安全対策の「採用」自体を承認するものではない。** 実装着手前に、
利用者が上記対応方針(または代替案)を選択・承認する必要がある。

## 検討した代替案

### (i) Binance側はlong-onlyのまま維持する

**不採用の理由**: 本ADRの出発点である「Binance側でもショート対応する」という
利用者の目的そのものを満たせない。ただしMargin Testnetが存在しないという
リスクの重さを踏まえると、この案(何もしない)を再検討する余地は残る
(下記「将来の見直し条件」参照)。

### (ii) Isolated Marginも含めて最初から対応する

**不採用の理由**: Isolated MarginはCross Marginとエンドポイント体系が別物
(`/sapi/v1/margin/isolated/*`)であるだけでなく、銘柄ごとに証拠金が分離される
ため、現行の「1 `exchange_connection` = 1取引所接続」「1 `trading_account` =
1口座」という前提と衝突し、データモデルの設計スコープが大きく膨らむ
(前回調査で「Isolated per-symbol collateral silosは現行の1接続=1口座モデルに
綺麗に収まらない」ことを確認済み)。Cross Margin単体でも本番APIでしか検証
できないという重大リスクを既に抱えているため、段階的にCross→Isolatedの順で
導入する方が影響範囲を制御しやすい。

## 設計: `margin_health_ratio`(取引所非依存の正規化フィールド)

### OANDA

```
health = 1 - marginCloseoutPercent
```

`marginCloseoutPercent`は0(証拠金未使用)〜1.0以上(強制決済)のスケールで、
1.0がOANDAの強制決済閾値。この式は`health=1.0`を「証拠金未使用の安全上限」、
`health<=0`を「強制決済相当」に対応させる。OANDA側は既存の
`account_snapshot.margin_closeout_percent`列(既存DBに存在)から計算できる。

### Binance(たたき台・要検証)

```
health = (marginLevel - forceLiquidationBar) / (normalBar - forceLiquidationBar)
```

`marginLevel`は`GET /sapi/v1/margin/account`、`forceLiquidationBar`/`normalBar`は
`GET /sapi/v1/margin/tradeCoeff`から取得する(前回調査のドキュメント例値:
`normalBar=1.5`、`marginCallBar=1.3`、`forceLiquidationBar=1.1`)。この式は
`health=0`を`forceLiquidationBar`(強制決済)、`health=1.0`を`normalBar`
(「通常」水準、警告帯である`marginCallBar`より安全側)に対応させる。

**この式には2点の未検証の仮定が含まれており、本番API検証(前述の要承認事項)を
経て初めて確認できる:**

1. `marginLevel`と`marginCallBar`/`forceLiquidationBar`が同一スケール上で直接
   比較可能な値であることは、Binance公式ドキュメントのどこにも明記されていない
   (前回調査で確認)。`tradeCoeff`のenum定義ページには各バーが
   「liquidation margin ratio」「margin call margin ratio」「initial margin
   ratio」と意味づけられているのみで、`marginLevel`が同じ「ratio」の実体である
   ことを示す数式や実例は見つからなかった。
2. `tradeCoeff`の値(バー)がVIPレベルや銘柄によって変動するかどうかも
   ドキュメント上不明(前回調査で確認)。本設計は`tradeCoeff`を**都度APIから
   取得**する前提(定数としてハードコードしない)とすることでこのリスクを
   軽減するが、それでも運用開始前に実際の応答値を確認する必要がある。

OANDA側は`health`が1.0を上限とする一方、Binance側は`marginLevel`が
`normalBar`を上回る限り1.0を超えて上昇し続ける(上限がない)。**この非対称性は
意図的に解消していない**(無理に揃えるより、各取引所の実際のリスク特性を
正直に反映する方を優先した)。閾値判定(下記)側で吸収する。

### `trading_halt`との接続(たたき台・未承認)

`05_アーキテクチャと移行計画.md`の「取引停止マトリクス」(warning/entry_halted/
all_trading_halted/emergency_stopped)に対する`margin_health_ratio`の閾値案:

| `margin_health_ratio` | 対応する停止レベル |
|---:|---|
| 1.0以上 | 停止なし |
| 0.5以上1.0未満 | `warning` |
| 0.2以上0.5未満 | `entry_halted` |
| 0超0.2未満 | `all_trading_halted` |
| 0以下 | `emergency_stopped` |

数値(0.5/0.2等の境界)は根拠のない暫定値であり、正式決定はRisk Gate実装タスクで
行う。

## 新規テーブル案

### `margin_loan`(新設)

借入元本(負債)の管理。既存の`ledger_entry.entry_type=financing`は**金利費用の
損益計上**を担い(既存カラムのまま変更しない)、`margin_loan`は**元本残高の増減**
という損益中立な貸借対照表項目を扱う、という役割分担にする(前回調査で
指摘された「元本とP&L計上を混在させるとコスト報告が壊れる」という懸念への対応)。

列案:

| 列 | 型 | 備考 |
|---|---|---|
| id | uuid | PK |
| account_id | uuid | FK -> trading_account |
| asset | varchar(32) | 借入資産 |
| event_type | text | `borrow` / `repay` / `interest_accrual` |
| amount | numeric(38,18) | 正数(移動量。符号の意味はevent_typeに従う) |
| running_balance | numeric(38,18) | このイベント後の借入残高 |
| created_at | timestamptz | |

`interest_accrual`をここに含めるか、`ledger_entry(financing)`のみで足りるかは
未確定(重複しうる)。たたき台としては、`margin_loan`は**残高追跡専用**とし、
`interest_accrual`イベントは残高を更新するだけで金額そのものの損益計上は
既存`ledger_entry(financing)`側で行う、という分離を提案する。正式な設計は
実装タスクで確定する。

### `account_valuation_snapshot` は新設しない(既存`account_snapshot`を拡張する)

**依頼文にあった`account_valuation_snapshot`(equity/unrealized_pnl/
realized_pnl_cumulative/margin_used/drawdown_from_peak/consecutive_loss_count)は、
調査の結果、新設しない方針に変更する。**

理由: `database/postgresql_schema_v0.1.sql`(2026-08-16の初回migrationで
既に適用済み)に、ほぼ同じ役割の`account_snapshot`テーブルが既に存在する
(`id, account_id, captured_at, balances(jsonb), equity, unrealized_pnl,
margin_used, margin_available, margin_call_percent, margin_closeout_percent,
source`)。新設すると、この既存テーブルと目的が重複する。**ただしこの
`account_snapshot`テーブルには現時点でORMマッピングが存在せず
(`app/models/trading.py`等のどこにも`AccountSnapshot`クラスが無い)、
今回の調査で初めて気づいた未整備状態である**(前回タスクで見つかった
`order_status_history`と同種の「DBに存在するがORM未対応」の抜け)。

提案: `AccountSnapshot`のORMマッピングを追加した上で、以下の列を追加する
migrationを実装タスクで行う。

| 追加列 | 型 | 備考 |
|---|---|---|
| margin_health_ratio | numeric(20,10) | 本書§2の正規化フィールド。nullable(OANDA/Binance以外や未算出時) |
| realized_pnl_cumulative | numeric(38,18) | Risk Gate用。既存の`margin_call_percent`/`margin_closeout_percent`はOANDA固有語彙のまま残し、削除しない |

`drawdown_from_peak`と`consecutive_loss_count`は、`account_snapshot`の時系列
行から都度計算する(ピーク保持のための追加状態を持たない)か、専用の小さな
「現在のリスク状態」テーブル(前回実装した`instrument_spread`と同じ、
1口座1行のUPSERT型)を別途持つかで判断が分かれる。**これはRisk Gate実装
タスクのスコープであり、本ADRでは選択肢の提示のみに留め、決定しない。**

記録タイミング(たたき台): 各fill発生時(`order_flow.py`の`place_order`内、
損益が動く瞬間を正確に捉える)と、定期スケジュール(価格変動によるevity/
unrealized_pnlの変化を捕捉するため。間隔はRisk Gateタスクで確定)の**両方**を
提案する。fillのみだと、ポジション保有中の市場変動によるドローダウンを見逃す。

## `exchange_connection`/`external_account`のスキーマ調査

`app/models/connections.py`の`ExchangeConnection`/`ExternalAccount`を再確認した。

- `ExchangeConnection`には`capabilities: JSONB`と`environment: Text`が既に
  あり、spot/margin区別の追加自体はこの`capabilities`に載せることも、
  `ExternalAccount`の`hedging_enabled`/`margin_rate`/`gslo_mode`
  (OANDA向けに既に型付き列として追加済み)と同じ流儀で型付き列を追加する
  こともできる。**既存の流儀との一貫性(型付き列)を優先するか、JSONB活用で
  スキーマ変更を避けるかは判断が分かれる点であり、実装タスクで決定する。**
- `TradingPosition.side`(long/short/net)は既にショート方向を表現できる
  (DB制約上、実装で制限しているだけ)。Cross Margin対応時にこの制約自体を
  緩める必要はない。

## 影響範囲

- 本ADR自体はコード・スキーマへの変更を一切加えない。
- Cross Margin実装タスクが承認されるまで、Binance側はlong-onlyのまま
  (`app/trading/application/order_flow.py`の現行動作を変更しない)。
- 「要・利用者承認事項」(本番API検証)が承認されない限り、Cross Margin対応の
  実装自体に着手すべきではない(承認なしに書き込み系APIへ実資金でアクセス
  するべきではない)。

## 将来の見直し条件

- Binanceが将来Margin Testnetを公式提供した場合、本ADRの最大のリスク前提
  (本番APIでしか検証できない)が解消されるため、本番API検証の要承認事項を
  見直す。
- 本番API検証で`marginLevel`と`tradeCoeff`の数値関係が確認でき次第、
  §2のBinance側`margin_health_ratio`式を確定値として更新する。
- Isolated Margin対応、他取引所(将来追加される場合)への同様の拡張を検討する
  際は、本ADRの§「検討した代替案」(ii)の不採用理由を再評価する。
