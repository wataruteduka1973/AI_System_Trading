import { useEffect, useRef, useState } from 'react'
import { Route, Routes, useLocation, useNavigate } from 'react-router'
import AppShell from './app/AppShell'
import { connectionPath, resolveAppRoute } from './app/routes'
import ConnectionManagementPage from './pages/ConnectionManagementPage'
import ExchangeMarketPage from './pages/ExchangeMarketPage'
import HomePage from './pages/HomePage'
import NotFoundPage from './pages/NotFoundPage'
import { useHealth } from './features/health/useHealth'
import HealthPanel from './features/health/HealthPanel'
import { useWorkspaces } from './features/workspaces/useWorkspaces'
import WorkspaceSelector from './features/workspaces/WorkspaceSelector'
import { useInstruments } from './features/instruments/useInstruments'
import InstrumentsPanel from './features/instruments/InstrumentsPanel'
import { useConnections } from './features/connections/useConnections'
import ConnectionsPanel, { ConnectionRegistrationForms } from './features/connections/ConnectionsPanel'
import { useMarketData } from './features/market-data/useMarketData'
import { useMarketStream } from './features/market-data/useMarketStream'
import MarketDataPanel from './features/market-data/MarketDataPanel'
import { apiBaseUrl } from './lib/api'
import { setOwnerTokenForErrorReporting } from './lib/errorReporting'
import './App.css'

function App() {
  const location = useLocation()
  const navigate = useNavigate()
  const route = resolveAppRoute(location.pathname)
  const { apiHealth, dbHealth, refreshHealth } = useHealth()
  const [ownerToken, setOwnerToken] = useState('')
  useEffect(() => {
    setOwnerTokenForErrorReporting(ownerToken)
  }, [ownerToken])
  const instruments = useInstruments(ownerToken)
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
    loadWorkspaces,
    selectWorkspace: selectWorkspaceState,
  } = useWorkspaces(route.workspaceId, ownerToken, onWorkspaceSelected)

  const connections = useConnections(ownerToken, selectedWorkspaceId, setWorkspaceMessage)
  useEffect(() => {
    connectionsLoadRef.current = connections.load
  }, [connections.load])

  const selectWorkspace = async (workspaceId: string) => {
    await selectWorkspaceState(workspaceId)
    navigate(workspaceId ? connectionPath(workspaceId) : '/')
  }

  const marketData = useMarketData(ownerToken, selectedWorkspaceId, activeInstrumentId)
  const marketStream = useMarketStream(
    ownerToken,
    selectedWorkspaceId,
    activeInstrumentId,
    marketData.timeframe,
    route.kind === 'market',
    marketData.reloadMarketData,
  )

  return (
    <AppShell workspaceId={selectedWorkspaceId}>
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
        <Route path="*" element={<NotFoundPage />} />
      </Routes>

      {route.kind !== 'not-found' && (
      <section className="workspace-panel">
        <WorkspaceSelector
          ownerToken={ownerToken}
          onOwnerTokenChange={setOwnerToken}
          onLoadWorkspaces={() => void loadWorkspaces()}
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
        onStartBackfill={() => void marketData.startBackfill()}
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

      {route.kind === 'home' && <button type="button" onClick={refreshHealth}>
        再確認
      </button>}
      {route.kind === 'home' && <p className="endpoint">API: {apiBaseUrl}</p>}
    </main>
    </AppShell>
  )
}

export default App
