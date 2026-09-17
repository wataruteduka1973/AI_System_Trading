# リアルタイム市場データ配信 詳細設計

対象: Horizon 2「リアルタイム観測と運用可視性」。実装計画・作業単位・受入試験は
[Plan](../../plans/realtime-market-data-stream.md) を正とする。本書は②〜⑦で参照する
event/ticket形式、feedライフサイクル、正規化方針を定義する。

## 1. 目的と境界

取引所の価格変動をブラウザのチャートへ低遅延で反映しつつ、確定足の永続化は既存の
Application/Worker経路に一本化する。本書が扱うのは「取引所stream→正規化→ブラウザWS」の
経路のみであり、確定足のPostgreSQL保存経路（upsert、gap/coverage判定）は変更しない。

非目標: 複数APIプロセスへのスケールアウト、取引所への注文送信、Binance Public履歴の導入。

## 2. 現行との差分

現行は履歴RESTのみで、`app/api/routes/market_data.py`が読み取り専用の候補足一覧・coverageを
返す。本設計は同じAPIプロセスに以下を追加する。

```text
既存: React UI --HTTP--> FastAPI routes --SQLAlchemy--> PostgreSQL
追加: React UI --WS(ticket)--> FastAPI stream endpoint <--in-memory pub/sub-- feed adapter
                                                              |
                                                    OANDA PricingStream / Binance kline_socket
```

`app/market_data/worker/`（独立プロセス）は無変更。確定足の最終的な正としての保存は
引き続きWorkerの`ExecuteMarketDataPage`が担う。stream側の"finalized" eventはあくまで
チャートの即時反映用であり、DB書き込みの正ではない（次項）。

## 3. Feedの単位とライフサイクル

feedキー = `(exchange, symbol, timeframe)`。Workspace境界はticket発行時とWS接続時の両方で
確認するが、同一feedへの取引所接続自体はWorkspace非依存で共有する（価格データ自体に
Workspace固有情報は含まれないため）。

- 起動: 最初の購読者がticketを提示してWS接続した時点でfeedを遅延生成する。
- 共有: 同一feedキーへの2人目以降の購読者は既存feedにattachし、新しい取引所接続を張らない
  （RT-04）。
- 終了: 最後の購読者が切断してから grace period（既定30秒、設定可能）が経過したら
  取引所接続をteardownする。grace period中の再購読はfeedを再利用する（RT-05/RT-06）。
- feed内部状態: `sequence`（feedごとの単調増加カウンタ、生成時0から開始）、直近event
  ring buffer（既定200件、再接続時の即時再送用）、feed生成時刻（クライアントが
  「feedが再生成されたか」をticket応答から判定する材料にする）。

## 4. Ticket

`POST /workspaces/{workspace_id}/market-stream-tickets`

- 入力: `exchange`, `symbol`（またはinstrument_id）, `timeframe`。
- 認可確認: 既存のPageAccess相当の確認（Workspace所属、選択口座のverified状態、
  Practice/Testnet環境）を流用する。credential自体は復号しない（stream接続用の資格情報は
  APIプロセスが別途、既存のsecret参照経由で取得する。ticketには含めない）。
- ticket本体: PyJWT（`pyproject.toml`へ追加）による署名付きJWT。claims:
  `jti`（一意ID）, `workspace_id`, `exchange`, `symbol`, `timeframe`, `exp`（発行から60秒）。
  署名鍵はAPIプロセスのローカル設定値（既存の`Settings`経由、DBやログに出さない）。
- 検証・失効: WS接続時に署名とexpを検証し、`jti`をAPIプロセス内のin-memory setに
  「使用済み」として記録する（TTLはticketのexpと同じ60秒で自動失効させ、setの無限増大を防ぐ）。
  2回目の使用は拒否する（RT-03）。DBテーブルは追加しない
  （ticketの検証はAPIプロセス内で完結するため、Workerのようなプロセス間共有は不要）。
- Owner tokenはticket発行APIの認証にのみ使う。WS接続のURLにはticket以外の秘密を含めない
  （既存方針を維持）。

## 5. Event形式

```json
{
  "event_id": "uuid",
  "workspace_id": "...",
  "exchange": "oanda|binance",
  "symbol": "USD_JPY|BTCJPY",
  "timeframe": "1m",
  "sequence": 123,
  "event_type": "provisional_update|candle_finalized|heartbeat|gap_notice",
  "open_time": "2026-09-17T00:00:00Z",
  "ohlcv": { "open": "...", "high": "...", "low": "...", "close": "...", "volume": "..." },
  "source": "oanda_practice|binance_testnet",
  "quality": "provisional|final"
}
```

`heartbeat`は30秒間隔でOHLCVなしで送る（接続生存確認・遅延計測用）。`gap_notice`は
取引所側の切断・空応答検知時に送り、フロントの「取得遅延中」表示に使う（RT-11）。

（③実装時の明確化: 上記JSONは⑤のWS終端がクライアントへ送信する外部event形式であり、
`workspace_id`はWS終端でticketから解決した値をここで初めて付与する。③で実装した内部の
`FeedHub`/`CandleStreamEvent`表現は、feed自体がWorkspace非依存であることに合わせて
`workspace_id`を持たない。また`gap_notice`向けに`reason_code`フィールド（例:
`oanda_unreachable`, `oanda_authentication_failed`）を内部表現に追加した。安全な
分類コードのみを許可し、生の例外メッセージは含めない方針は本セクション冒頭の記載通り。）

## 6. OANDA tick→candle正規化とBinance kline

- OANDA: `PricingStream`は価格tick（bid/ask、timestamp）のみを返す。blocking generatorのため
  `asyncio.to_thread`で駆動し、受け取ったtickをasyncio.Queueへ渡してfeedのasyncタスク側で
  処理する。timeframe境界（例: 1分足なら`open_time`の分が変わった瞬間）を跨いだ最初のtickで
  直前のbucketを`candle_finalized`として確定し、新しいbucketを`provisional_update`として
  開始する。ミッドプライス（bid/ask平均）をOHLCVに使う（既存の履歴REST側の丸め方針と
  整合させる。詳細は実装時に既存`app/exchanges/oanda.py`の価格変換ロジックと突き合わせる）。
- Binance: `BinanceSocketManager.kline_socket(symbol, interval)`はtimeframeごとのkline
  messageをネイティブに返し、`k.x`（is_closed）フィールドで確定/未確定を判別できるため
  tick合成は不要。`k.o/h/l/c/v`をそのままOHLCVへマッピングする。

（③実装時の明確化: `volume`はOANDA自身のREST candle APIと同じ「bucket内のtick件数」を
採用した（実際の出来高データはPricingStreamからは取得できないため）。また、timeframe境界の
判定はUTC epochに揃えたbucket flooring（例: 1分足なら`floor(tick_time, 60s)`）による
MVP簡略化であり、OANDAブローカーの日足境界（NYクローズ基準）とは一致しない。日足以上の
timeframeを扱う単位が出てきた際に、ブローカー日境界への対応要否を再検討する。）

## 7. 再接続とgap-fill

クライアントは受信event中の最大`sequence`を保持する。WS切断時:

1. 新しいticketを取得し、WS再接続する（ticketは1接続1回のみ有効なため使い回さない）。
2. サーバはWS接続確立時、feedの生成時刻がクライアントの記憶する前回接続時より新しければ
   「feed再生成済み」と応答し、クライアントは最新確定足時刻からの既存REST API
   （`GET .../candles?before=...`）でgap-fillしてからprovisional購読を再開する。
3. feedが生存中（API未再起動、grace period内の再接続等）であれば、ring bufferに
   クライアントの`last_sequence`より新しいeventが残っている場合はそれを即時再送する
   （RT-07）。ring bufferの範囲を超えていれば2.のREST gap-fillにフォールバックする。

## 8. 状態・失敗

- 取引所への接続失敗・429・認証失敗は`gap_notice`＋feed状態を`delayed`または`disconnected`
  にして購読者へ通知する。安全なエラーコードのみを使い、資格情報や生の例外メッセージは
  event/ログに出さない（Worker側の`page_errors.py`の分類方針を踏襲する）。
- Workspaceの接続無効化・口座変更が発生した場合、該当Workspaceの購読者のみticket再検証で
  弾く（feed自体はWorkspace非依存のため他購読者には影響しない）。

## 9. 技術根拠・依存関係

- `websockets`, `tenacity`, `orjson`は`requirements.txt`に将来利用を見越して既に記載済み
  （本機能で実装・import後に`pyproject.toml`へ追加する）。`tenacity`は取引所再接続の
  指数バックオフに使う。`orjson`はevent shapeが単純なJSONのため、標準`json`との比較で
  導入要否を③実装時に判断する（過剰導入を避ける）。
- `PyJWT[crypto]`をticket署名に採用する。将来のOIDC導入時にも同じライブラリを継続利用できる。
- `python-binance`のAsync/WebSocket機能は`aiohttp`（python-binanceの`Requires-Dist`に含まれる
  transitive依存）を必要とするが、`app/exchanges/binance.py`が既に`from binance import
  AsyncClient`で同じimport経路（`binance/__init__.py`が`AsyncClient`と`BinanceSocketManager`
  を同じimport文で読み込む）を使っており、既存CIのPython 3.13.15上で問題なく動作している。
  追加の依存解消作業は不要（2026-09-17訂正: 当初`async_timeout`欠如によるimport失敗を
  報告したが、Linux VM側の素のPython 3.10で誤って検証したことによる誤りだった。aiohttp本体は
  `sys.version_info >= (3, 11)`で`asyncio`を`async_timeout`として代用するガードを持つ）。
  ④では当初`websockets`を自コードから直接importする想定だったが、実装してみると
  `BinanceSocketManager`/`ReconnectingWebsocket`が接続・エラー処理を完全に抽象化しており
  生の`websockets`例外や型がアダプタ側コードへ一切露出しないことが判明したため、直接import
  は発生せず`pyproject.toml`への追加は不要だった（詳細はplan docのstatus log④を正とする）。
  （`aiohttp`は`python-binance`経由のtransitive依存のままとし、直接importしない限り追加しない）。
