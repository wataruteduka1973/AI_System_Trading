/** Mirrors `app.schemas.backtests` (app/api/routes/backtests.py). */

export type BacktestMetrics = {
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: string
  gross_profit: string
  gross_loss: string
  net_pnl: string
  total_fees: string
  profit_factor: string | null
  max_drawdown: string
  max_drawdown_pct: string
}

type BaselineComparison = {
  baseline: BacktestMetrics
  net_pnl_diff: string
  win_rate_diff: string
  max_drawdown_pct_diff: string
  profit_factor_diff: string | null
}

type BacktestSummaryMetrics = {
  metrics?: BacktestMetrics
  baseline_comparison?: BaselineComparison
}

export type BacktestRun = {
  id: string
  workspace_id: string
  strategy_version_id: string
  risk_profile_version_id: string
  dataset_snapshot_id: string
  parameters: Record<string, unknown>
  code_version: string
  status: string
  summary_metrics: BacktestSummaryMetrics
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export type BacktestTrade = {
  id: string
  backtest_run_id: string
  sequence_no: number
  instrument_id: string
  side: string
  entry_time: string
  exit_time: string | null
  entry_price: string
  exit_price: string | null
  quantity: string
  fees: string
  realized_pnl: string | null
}

/** Mirrors `app.schemas.backtests.BacktestEquityCurveRead`: downsampled, empty for a run
 * made before curves were stored. */
export type EquityCurve = {
  initial_equity: string | null
  points: { time: string; equity: string }[]
}

/** What the run-detail view shows for the run the user picked. */
export type BacktestRunDetail = {
  runId: string
  trades: BacktestTrade[]
  equityCurve: EquityCurve
}
