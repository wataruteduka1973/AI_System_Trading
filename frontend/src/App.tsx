import { useEffect, useRef } from 'react'
import { Route, Routes, useLocation, useNavigate } from 'react-router'
import AppShell from './app/AppShell'
import { connectionPath, resolveAppRoute } from './app/routes'
import ConnectionManagementPage from './pages/ConnectionManagementPage'
import ExchangeMarketPage from './pages/ExchangeMarketPage'
import HomePage from './pages/HomePage'
import NotFoundPage from './pages/NotFoundPage'
import TradingPage from './pages/TradingPage'
import BacktestPage from './pages/BacktestPage'
import { useHealth } from './features/health/useHealth'
import HealthPanel from './features/health/HealthPanel'
import { useAuth } from './features/auth/useAuth'
import { useWorkspaces } from './features/workspaces/useWorkspaces'
import WorkspaceSelector from './features/workspaces/WorkspaceSelector'
import { useInstruments } from './features/instruments/useInstruments'
import InstrumentsPanel from './features/instruments/InstrumentsPanel'
import { useConnections } from './features/connections/useConnections'
import ConnectionsPanel, { ConnectionRegistrationForms } from './features/connections/ConnectionsPanel'
import { useMarketData } from './features/market-data/useMarketData'
import { useMarketStream } from './features/market-data/useMarketStream'
import MarketDataPanel from './features/market-data/MarketDataPanel'
import { useTrading } from './features/trading/useTrading'
import TradingPanel, { TradingForms } from './features/trading/TradingPanel'
import { useBacktests } from './features/backtests/useBacktests'
import BacktestPanel, { BacktestForm } from './features/backtests/BacktestPanel'
import { apiBaseUrl } from './lib/api'
import './App.css'

function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const route = resolveAppRoute(location.pathname)
  const { apiHealth, dbHealth, refreshHealth } = useHealth()
  const auth = useAuth()
  const instruments = useInstruments()
  const {
    workspaceInstruments,
    instrumentMessage,
    selectedInstrumentId,
    setSelectedInstrumentId,
  } = instruments
  const visibleInstruments = route.kind === 'market'
    ? workspaceInstruments.filter((instrument) => instrument.exchange_code === route.exchange)
    : workspaceInstruments
  const activeInstrumentId = visibleInstruments.some(
    (instrument) => instrument.id === selectedInstrumentId,
  ) ? selectedInstrumentId : visibleInstruments[0]?.id ?? ''

  /** `useConnections` needs `setWorkspaceMessage`, which only exists once `useWorkspaces` is
   * called below — but `useWorkspaces` needs `onWorkspaceSelected` before that. This ref breaks
   * the cycle: `onWorkspaceSelected` reads it at call time (always after render), so it is fine
   * that `connections.load` is only assigned into it after `useConnections` runs. */
  const connectionsLoadRef = useRef<(workspaceId: string) => Promise<string | undefined>>(
    async () => undefined,
  )

  const onWorkspaceSelected = async (workspaceId: string): Promise<string | undefined> => {
    const [connectionsMessage] = await Promise.all([
      connectionsLoadRef.current(workspaceId),
      instruments.load(workspaceId),
    ])
    return connectionsMessage
  }

  const {
    workspaces,
    selectedWorkspaceId,
    workspaceMessage,
    setWorkspaceMessage,
    selectWorkspace: selectWorkspaceState,
  } = useWorkspaces(route.workspaceId, auth.memberships, onWorkspaceSelected)

  const connections = useConnections(selectedWorkspaceId, setWorkspaceMessage)
  useEffect(() => {
    connectionsLoadRef.current = connections.load
  }, [connections.load])

  const selectWorkspace = async (workspaceId: string) => {
    await selectWorkspaceState(workspaceId)
    navigate(workspaceId ? connectionPath(workspaceId) : '/')
  }

  const marketData = useMarketData(selectedWorkspaceId, activeInstrumentId)
  const marketStream = useMarketStream(
    selectedWorkspaceId,
    activeInstrumentId,
    marketData.timeframe,
    route.kind === 'market',
    marketData.reloadMarketData,
  )

  const trading = useTrading(selectedWorkspaceId)
  const backtests = useBacktests(selectedWorkspaceId)

  if (auth.status === 'loading') {
    return (
      <main className="dashboard-shell">
        <p>認証状態を確認しています…</p>
      </main>
    )
  }

  if (auth.status === 'unauthenticated') {
    return (
      <main className="dashboard-shell">
        <section className="workspace-panel">
          <h2>ログインが必要です</h2>
          <p className="panel-description">続行するにはログインしてください。</p>
          <button type="button" onClick={auth.login}>
            ログイン
          </button>
        </section>
      </main>
    )
  }

  return (
    <AppShell workspaceId={selectedWorkspaceId} user={auth.user} onLogout={() => void auth.logout()}>
    <main className="dashboard-shell">
      <Routes>
        <Route
          path="/"
          element={(
            <HomePage>
              <HealthPanel apiHealth={apiHealth} dbHealth={dbHealth} />
            </HomePage>
          )}
        />
        <Route
          path="/workspaces/:workspaceId/connections"
          element={<ConnectionManagementPage>{null}</ConnectionManagementPage>}
        />
        <Route
          path="/workspaces/:workspaceId/markets/oanda"
          element={<ExchangeMarketPage exchange="oanda">{null}</ExchangeMarketPage>}
        />
        <Route
          path="/workspaces/:workspaceId/markets/binance"
          element={<ExchangeMarketPage exchange="binance">{null}</ExchangeMarketPage>}
        />
        <Route
          path="/workspaces/:workspaceId/trading"
          element={<TradingPage>{null}</TradingPage>}
        />
        <Route
          path="/workspaces/:workspaceId/backtests"
          element={<BacktestPage>{null}</BacktestPage>}
        />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>

      {route.kind !== 'not-found' && (
      <section className="workspace-panel">
        <WorkspaceSelector
          workspaces={workspaces}
          selectedWorkspaceId={selectedWorkspaceId}
          onSelectWorkspace={(workspaceId) => void selectWorkspace(workspaceId)}
          workspaceMessage={workspaceMessage}
        />
        <ConnectionsPanel
          visible={route.kind === 'connections'}
          connections={connections.connections}
          workspaceAccounts={connections.workspaceAccounts}
          onVerify={(connection) =>
            void (connection.environment === 'testnet'
              ? connections.verifyBinanceConnection(connection.id)
              : connections.verifyOandaConnection(connection.id))
          }
          onDisable={(connection) => void connections.manageConnection(connection, 'disable')}
          onDelete={(connection) => void connections.manageConnection(connection, 'delete')}
          onSelectAccount={(account) => void connections.selectAccount(account)}
        />
        <TradingPanel
          visible={route.kind === 'trading'}
          tradingAccounts={trading.tradingAccounts}
          bots={trading.bots}
          latestRuns={trading.latestRuns}
          onCommand={(bot, command) => void trading.runBotCommand(bot, command)}
        />
        <BacktestPanel
          visible={route.kind === 'backtests'}
          backtests={backtests.backtests}
          selectedRunTrades={backtests.selectedRunTrades}
          onViewTrades={(run) => void backtests.loadTrades(run)}
        />
      </section>
      )}

      {route.kind === 'connections' && selectedWorkspaceId && (
        <InstrumentsPanel
          hasSelectedAccount={connections.workspaceAccounts.some((account) => account.selected)}
          onSync={() => void instruments.syncInstruments(selectedWorkspaceId)}
          instrumentMessage={instrumentMessage}
          workspaceInstruments={workspaceInstruments}
        />
      )}

      <MarketDataPanel
        visible={route.kind === 'market' && Boolean(selectedWorkspaceId)}
        visibleInstruments={visibleInstruments}
        activeInstrumentId={activeInstrumentId}
        onSelectInstrument={setSelectedInstrumentId}
        selectedWorkspaceId={selectedWorkspaceId}
        timeframe={marketData.timeframe}
        onTimeframeChange={marketData.setTimeframe}
        submittingMarketAction={marketData.submittingMarketAction}
        onStartAutomaticCollection={() => void marketData.setAutomaticCollection(true)}
        onStopAutomaticCollection={() => void marketData.setAutomaticCollection(false)}
        marketDataMessage={marketData.marketDataMessage}
        subscriptions={marketData.subscriptions}
        backfillJobs={marketData.backfillJobs}
        coverage={marketData.coverage}
        candles={marketData.candles}
        marketDataLoading={marketData.marketDataLoading}
        olderCandlesLoading={marketData.olderCandlesLoading}
        candleError={marketData.candleError}
        hasOlderCandles={marketData.hasOlderCandles}
        onLoadOlder={() => void marketData.loadOlderCandles()}
        displayedRange={marketData.displayedRange}
        onDisplayedRangeChange={marketData.setDisplayedRange}
        connectionStatus={marketStream.connectionStatus}
        lastDataAt={marketStream.lastDataAt}
        gapCount={marketStream.gapCount}
        lastGapReason={marketStream.lastGapReason}
        liveCandle={marketStream.liveCandle}
      />

      <ConnectionRegistrationForms
        visible={route.kind === 'connections' && Boolean(selectedWorkspaceId)}
        connections={connections.connections}
        connectionLabel={connections.connectionLabel}
        onConnectionLabelChange={connections.setConnectionLabel}
        oandaToken={connections.oandaToken}
        onOandaTokenChange={connections.setOandaToken}
        registrationMessage={connections.registrationMessage}
        verifiedAccounts={connections.verifiedAccounts}
        selectedOandaConnectionId={connections.selectedOandaConnectionId}
        onSelectedOandaConnectionIdChange={connections.setSelectedOandaConnectionId}
        onRegisterOanda={() => void connections.registerAndVerifyOanda()}
        binanceLabel={connections.binanceLabel}
        onBinanceLabelChange={connections.setBinanceLabel}
        binanceApiKey={connections.binanceApiKey}
        onBinanceApiKeyChange={connections.setBinanceApiKey}
        binanceSecretKey={connections.binanceSecretKey}
        onBinanceSecretKeyChange={connections.setBinanceSecretKey}
        binanceMessage={connections.binanceMessage}
        binanceAccounts={connections.binanceAccounts}
        selectedBinanceConnectionId={connections.selectedBinanceConnectionId}
        onSelectedBinanceConnectionIdChange={connections.setSelectedBinanceConnectionId}
        onRegisterBinance={() => void connections.registerAndVerifyBinance()}
      />

      <TradingForms
        visible={route.kind === 'trading' && Boolean(selectedWorkspaceId)}
        connections={connections.connections}
        workspaceInstruments={workspaceInstruments}
        tradingAccounts={trading.tradingAccounts}
        tradingMessage={trading.tradingMessage}
        accountConnectionId={trading.accountConnectionId}
        onAccountConnectionIdChange={trading.setAccountConnectionId}
        accountBaseCurrency={trading.accountBaseCurrency}
        onAccountBaseCurrencyChange={trading.setAccountBaseCurrency}
        onCreateTradingAccount={() => void trading.createTradingAccount()}
        depositAccountId={trading.depositAccountId}
        onDepositAccountIdChange={trading.setDepositAccountId}
        depositAmount={trading.depositAmount}
        onDepositAmountChange={trading.setDepositAmount}
        depositAsset={trading.depositAsset}
        onDepositAssetChange={trading.setDepositAsset}
        onCreateDeposit={() => void trading.createDeposit()}
        botName={trading.botName}
        onBotNameChange={trading.setBotName}
        botAccountId={trading.botAccountId}
        onBotAccountIdChange={trading.setBotAccountId}
        botInstrumentId={trading.botInstrumentId}
        onBotInstrumentIdChange={trading.setBotInstrumentId}
        botTimeframe={trading.botTimeframe}
        onBotTimeframeChange={trading.setBotTimeframe}
        onCreateBot={() => void trading.createBot()}
      />

      <BacktestForm
        visible={route.kind === 'backtests' && Boolean(selectedWorkspaceId)}
        workspaceInstruments={workspaceInstruments}
        backtestMessage={backtests.backtestMessage}
        instrumentId={backtests.instrumentId}
        onInstrumentIdChange={backtests.setInstrumentId}
        timeframe={backtests.timeframe}
        onTimeframeChange={backtests.setTimeframe}
        fromTime={backtests.fromTime}
        onFromTimeChange={backtests.setFromTime}
        toTime={backtests.toTime}
        onToTimeChange={backtests.setToTime}
        initialEquity={backtests.initialEquity}
        onInitialEquityChange={backtests.setInitialEquity}
        spread={backtests.spread}
        onSpreadChange={backtests.setSpread}
        walkForward={backtests.walkForward}
        onWalkForwardChange={backtests.setWalkForward}
        trainRatio={backtests.trainRatio}
        onTrainRatioChange={backtests.setTrainRatio}
        onCreateBacktest={() => void backtests.createBacktest()}
      />

      {route.kind === 'home' && <button type="button" onClick={refreshHealth}>
        再確認
      </button>}
      {route.kind === 'home' && <p className="endpoint">API: {apiBaseUrl}</p>}
    </main>
    </AppShell>
  )
}

export default App
