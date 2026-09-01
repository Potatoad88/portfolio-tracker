import { useCallback, useEffect, useState } from "react";
import FileDownloadOutlined from "@mui/icons-material/FileDownloadOutlined";
import InfoOutlined from "@mui/icons-material/InfoOutlined";
import Refresh from "@mui/icons-material/Refresh";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { api, money } from "../api";
import AppHeader from "../components/AppHeader";
import CashFlowDialog from "../components/CashFlowDialog";
import FundingTable from "../components/FundingTable";
import { HoldingsTable, LineChart, Section } from "../components/Portfolio";
import type {
  BrokerMetadata,
  Currency,
  Funding,
  Point,
  Position,
  Status,
  Summary,
  View,
} from "../types";

export default function BrokerPage({
  brokers,
  metadata,
  currency,
  view,
  dark,
  onCurrency,
  onNavigate,
  onToggleTheme,
}: {
  brokers: BrokerMetadata[];
  metadata: BrokerMetadata;
  currency: Currency;
  view: View;
  dark: boolean;
  onCurrency: (currency: Currency) => void;
  onNavigate: (view: View) => void;
  onToggleTheme: () => void;
}) {
  const broker = metadata.id;
  const [summary, setSummary] = useState<Summary | null>(null),
    [positions, setPositions] = useState<Position[]>([]),
    [funding, setFunding] = useState<Funding[]>([]),
    [history, setHistory] = useState<Point[]>([]),
    [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true),
    [refreshing, setRefreshing] = useState(false),
    [error, setError] = useState(""),
    [staleDismissed, setStaleDismissed] = useState(false),
    [reconciliationDismissed, setReconciliationDismissed] = useState(false);
  const [exportAnchor, setExportAnchor] = useState<HTMLElement | null>(null);
  const [cashFlowOpen, setCashFlowOpen] = useState(false),
    [notice, setNotice] = useState("");
  const [chartMetric, setChartMetric] = useState<
      "total_equity" | "performance_value"
    >("performance_value"),
    [range, setRange] = useState<"1M" | "3M" | "1Y" | "ALL">("1Y");
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const q = `?currency=${currency}&broker=${broker}`,
        b = `?broker=${broker}`;
      const [s, p, f, h, st] = await Promise.all([
        api<Summary>(`/api/summary${q}`),
        api<Position[]>(`/api/positions${q}`),
        metadata.capabilities.fundingHistory
          ? api<Funding[]>(`/api/funding${q}`)
          : Promise.resolve([]),
        api<Point[]>(`/api/history${q}`),
        api<Status>(`/api/sync/status${b}`),
      ]);
      setSummary(s);
      setPositions(p);
      setFunding(f);
      setHistory(h);
      setStatus(st);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load portfolio");
    } finally {
      setLoading(false);
    }
  }, [broker, currency, metadata.capabilities.fundingHistory]);
  useEffect(() => {
    load();
  }, [load]);
  const refresh = async () => {
    setRefreshing(true);
    setError("");
    try {
      await api(`/api/sync?broker=${broker}`, { method: "POST" });
      setStaleDismissed(false);
      setReconciliationDismissed(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refresh failed");
    } finally {
      setRefreshing(false);
    }
  };
  if (loading)
    return (
      <Box sx={{ height: "100vh", display: "grid", placeItems: "center" }}>
        <CircularProgress aria-label="Loading portfolio" />
      </Box>
    );
  const signed = (value?: string) =>
      `${Number(value || 0) >= 0 ? "+" : ""}${money(value, currency)}`,
    pnl = Number(summary?.overallPnl || 0);
  const cards = summary?.supportsContributions
    ? [
        { label: "Total equity", value: money(summary?.totalEquity, currency) },
        {
          label: "Net contributions",
          value: money(summary?.netContributions, currency),
          info: !metadata.capabilities.cashFlowSync
            ? "Completed deposits − withdrawals − withdrawal fees + applicable refunds."
            : "Verified SGD DDI deposits − bank withdrawals. All other cash flows are excluded.",
        },
        {
          label: "Overall P&L",
          value: signed(summary?.overallPnl),
          tone: pnl,
          info: "Total account equity − net contributions.",
        },
        {
          label: "Simple overall return",
          value:
            summary?.overallReturnPct == null
              ? "—"
              : `${Number(summary.overallReturnPct) >= 0 ? "+" : ""}${Number(summary.overallReturnPct).toFixed(2)}%`,
          tone: pnl,
          info: "Overall P&L ÷ net contributions × 100. This is not a time-weighted return or XIRR.",
        },
        { label: "Cash", value: money(summary?.cash, currency) },
        {
          label: "Holdings value",
          value: money(summary?.holdingsValue, currency),
        },
      ]
    : [
        { label: "Total equity", value: money(summary?.totalEquity, currency) },
        { label: "Cash", value: money(summary?.cash, currency) },
        {
          label: "Holdings value",
          value: money(summary?.holdingsValue, currency),
        },
        {
          label: "Unrealized P&L",
          value: signed(summary?.unrealizedPnl),
          tone: Number(summary?.unrealizedPnl || 0),
          info: "Sum of unrealized P&L returned for listed positions.",
        },
      ];
  const stocks = positions.filter((p) => ["STK", "ETF"].includes(p.asset_type)),
    funds = positions.filter((p) => p.asset_type === "FUND"),
    others = positions.filter(
      (p) => !["STK", "ETF", "FUND"].includes(p.asset_type),
    );
  const holdingsTotal = Number(summary?.holdingsValue || 0),
    lastPoint = history.length
      ? new Date(history.at(-1)!.captured_at).getTime()
      : 0,
    days = { "1M": 31, "3M": 93, "1Y": 366, ALL: Infinity }[range];
  const chartData =
    range === "ALL"
      ? history
      : history.filter(
          (point) =>
            new Date(point.captured_at).getTime() >=
            lastPoint - days * 86400000,
        );
  const exportCsv = (dataset: string) => {
    setExportAnchor(null);
    window.location.assign(
      `/api/export/${dataset}?currency=${currency}&broker=${broker}`,
    );
  };
  const chartMetricShown = summary?.supportsContributions
    ? chartMetric
    : "total_equity";
  return (
    <Box
      sx={{
        minHeight: "100vh",
        background: dark
          ? "radial-gradient(circle at 50% -20%,#282017 0,transparent 38%)"
          : "radial-gradient(circle at 50% -20%,#fff4e7 0,transparent 42%)",
      }}
    >
      <AppHeader
        brokers={brokers}
        view={view}
        currency={currency}
        dark={dark}
        onView={onNavigate}
        onCurrency={onCurrency}
        onToggleTheme={onToggleTheme}
        actions={
          <>
            <Chip
              size="small"
              label={metadata.configured ? "Live" : "Not configured"}
              color={metadata.configured ? "success" : "default"}
              variant="outlined"
            />
            {metadata.capabilities.exports && (
              <>
                <Button
                  variant="outlined"
                  startIcon={<FileDownloadOutlined />}
                  onClick={(event) => setExportAnchor(event.currentTarget)}
                >
                  Export
                </Button>
                <Menu
                  anchorEl={exportAnchor}
                  open={Boolean(exportAnchor)}
                  onClose={() => setExportAnchor(null)}
                >
                  <MenuItem onClick={() => exportCsv("positions")}>
                    Positions CSV
                  </MenuItem>
                  {metadata.capabilities.fundingHistory && (
                    <MenuItem onClick={() => exportCsv("funding")}>
                      {metadata.capabilities.cashFlowSync
                        ? "Deposits & withdrawals CSV"
                        : "Funding CSV"}
                    </MenuItem>
                  )}
                  <MenuItem onClick={() => exportCsv("history")}>
                    Portfolio history CSV
                  </MenuItem>
                </Menu>
              </>
            )}
            <Button
              variant="contained"
              startIcon={
                refreshing ? (
                  <CircularProgress size={16} color="inherit" />
                ) : (
                  <Refresh />
                )
              }
              onClick={refresh}
              disabled={refreshing || !metadata.configured}
            >
              Refresh
            </Button>
          </>
        }
      />
      <Container maxWidth="xl" sx={{ py: { xs: 3, md: 6 } }}>
        <Stack spacing={3}>
          {error && (
            <Alert severity="error" onClose={() => setError("")}>
              {error}
              {status?.lastSuccess &&
                " Previously synced data remains available."}
            </Alert>
          )}
          {notice && (
            <Alert severity="success" onClose={() => setNotice("")}>
              {notice}
            </Alert>
          )}
          {status?.stale && status.lastSuccess && !staleDismissed && (
            <Alert severity="warning" onClose={() => setStaleDismissed(true)}>
              Portfolio data may be stale. Refresh for current values.
            </Alert>
          )}
          {Math.abs(Number(summary?.reconciliationDifference || 0)) > 1 &&
            !reconciliationDismissed && (
              <Alert
                severity="warning"
                onClose={() => setReconciliationDismissed(true)}
              >
                Account reconciliation differs by{" "}
                {money(summary?.reconciliationDifference, currency)}. Cash plus
                listed positions does not match total equity; check Sync health
                and refresh.
              </Alert>
            )}
          {summary?.empty && (
            <Alert
              severity="info"
              action={
                <Button onClick={refresh} disabled={!metadata.configured}>
                  Sync now
                </Button>
              }
            >
              No portfolio data yet.
            </Alert>
          )}
          <Box>
            <Typography variant="h4">
              Your {metadata.displayName} portfolio
            </Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>
              Reporting in {currency} · Last {metadata.displayName} sync{" "}
              {status?.lastSuccess
                ? new Date(status.lastSuccess).toLocaleString()
                : "never"}
            </Typography>
          </Box>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "1fr 1fr",
                md: "repeat(3,1fr)",
                lg: "repeat(6,1fr)",
              },
              gap: 2,
            }}
          >
            {cards.map((card) => (
              <Card
                key={card.label}
                variant="outlined"
                sx={{
                  borderColor: "divider",
                  boxShadow: "0 10px 35px rgba(0,0,0,.04)",
                  transition: "transform .2s, box-shadow .2s",
                  "&:hover": {
                    transform: "translateY(-2px)",
                    boxShadow: "0 14px 40px rgba(0,0,0,.08)",
                  },
                }}
              >
                <CardContent sx={{ p: 2.5 }}>
                  <Box sx={{ display: "flex", alignItems: "center" }}>
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{ flexGrow: 1 }}
                    >
                      {card.label}
                    </Typography>
                    {card.info && (
                      <Tooltip title={card.info} arrow>
                        <IconButton
                          size="small"
                          aria-label={`How ${card.label.toLowerCase()} is calculated`}
                        >
                          <InfoOutlined sx={{ fontSize: 17 }} />
                        </IconButton>
                      </Tooltip>
                    )}
                  </Box>
                  <Typography
                    variant="h6"
                    sx={{
                      fontWeight: 760,
                      mt: 1,
                      color:
                        card.tone == null
                          ? "text.primary"
                          : card.tone >= 0
                            ? "success.main"
                            : "error.main",
                    }}
                  >
                    {card.value}
                  </Typography>
                </CardContent>
              </Card>
            ))}
          </Box>
          <Section
            title="Portfolio progress"
            subtitle={
              chartMetricShown === "performance_value"
                ? "Equity adjusted for deposits and withdrawals"
                : "Raw account equity over time"
            }
          >
            <Box sx={{ p: { xs: 2, md: 3 } }}>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                justifyContent="space-between"
                spacing={1.5}
                sx={{ mb: 2 }}
              >
                {summary?.supportsContributions ? (
                  <ToggleButtonGroup
                    size="small"
                    exclusive
                    value={chartMetric}
                    onChange={(_, value) => value && setChartMetric(value)}
                  >
                    <ToggleButton value="performance_value">
                      Performance
                    </ToggleButton>
                    <ToggleButton value="total_equity">Equity</ToggleButton>
                  </ToggleButtonGroup>
                ) : (
                  <Typography variant="body2" color="text.secondary">
                    History starts with the first successful broker sync.
                  </Typography>
                )}
                <ToggleButtonGroup
                  size="small"
                  exclusive
                  value={range}
                  onChange={(_, value) => value && setRange(value)}
                >
                  {(["1M", "3M", "1Y", "ALL"] as const).map((value) => (
                    <ToggleButton value={value} key={value}>
                      {value === "ALL" ? "All" : value}
                    </ToggleButton>
                  ))}
                </ToggleButtonGroup>
              </Stack>
              <LineChart
                data={chartData}
                currency={currency}
                metric={chartMetricShown}
              />
              {summary?.supportsContributions && (
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ mt: 1, color: "text.secondary" }}
                >
                  <Box
                    sx={{
                      width: 8,
                      height: 8,
                      borderRadius: 0.5,
                      bgcolor: "primary.main",
                    }}
                  />
                  <Typography variant="caption">Funding activity</Typography>
                </Stack>
              )}
            </Box>
          </Section>
          <Section
            title="Stocks & ETFs"
            subtitle={`${stocks.length} current holding${stocks.length === 1 ? "" : "s"}`}
          >
            <HoldingsTable
              rows={stocks}
              currency={currency}
              total={holdingsTotal}
              empty="No stock or ETF holdings"
            />
          </Section>
          {funds.length > 0 && (
            <Section
              title="Money market & funds"
              subtitle={`${funds.length} current fund holding${funds.length === 1 ? "" : "s"}`}
            >
              <HoldingsTable
                rows={funds}
                currency={currency}
                total={holdingsTotal}
                empty="No money-market or fund holdings"
              />
            </Section>
          )}
          {others.length > 0 && (
            <Section
              title="Other holdings"
              subtitle={`${others.length} current holding${others.length === 1 ? "" : "s"}`}
            >
              <HoldingsTable
                rows={others}
                currency={currency}
                total={holdingsTotal}
                empty="No other holdings"
              />
            </Section>
          )}
          {metadata.capabilities.fundingHistory && (
            <Section
              title={
                !metadata.capabilities.cashFlowSync
                  ? "Funding history"
                  : "Deposits & withdrawals"
              }
              subtitle={`${funding.length} verified transaction${funding.length === 1 ? "" : "s"}`}
              initial={false}
            >
              <>
                {metadata.capabilities.cashFlowSync && (
                  <Box
                    sx={{
                      p: 2,
                      borderBottom: 1,
                      borderColor: "divider",
                      display: "flex",
                      alignItems: "center",
                      gap: 2,
                      flexWrap: "wrap",
                    }}
                  >
                    <Button
                      variant="outlined"
                      onClick={() => setCashFlowOpen(true)}
                      disabled={!metadata.configured}
                    >
                      Fetch cash flow
                    </Button>
                    <Typography variant="body2" color="text.secondary">
                      {"Last cash-flow sync: " +
                        (status?.cashFlowLastSuccess
                          ? new Date(
                              status.cashFlowLastSuccess,
                            ).toLocaleString()
                          : "never")}
                    </Typography>
                  </Box>
                )}
                <FundingTable
                  funding={funding}
                  currency={currency}
                  cashFlowSync={metadata.capabilities.cashFlowSync}
                />
              </>
            </Section>
          )}
          <Section
            title="Sync health"
            subtitle={`Latest ${metadata.displayName} data-source status`}
            initial={false}
          >
            <Box
              sx={{
                p: { xs: 2, md: 3 },
                display: "flex",
                gap: 1.25,
                flexWrap: "wrap",
              }}
            >
              {Object.entries(status?.components || {}).map(([name, value]) => (
                <Chip
                  key={name}
                  label={`${name.replaceAll("_", " ")}: ${value}`}
                  color={
                    value === "ok"
                      ? "success"
                      : value.includes("fallback")
                        ? "warning"
                        : "default"
                  }
                  variant="outlined"
                />
              ))}
              {metadata.capabilities.cashFlowSync &&
                status?.cashFlowLastSuccess && (
                  <Chip
                    label={`cash flow last synced: ${new Date(status.cashFlowLastSuccess).toLocaleString()}`}
                    color="success"
                    variant="outlined"
                  />
                )}
              {!Object.keys(status?.components || {}).length && (
                <Typography color="text.secondary">
                  Run a fresh sync to record component health.
                </Typography>
              )}
            </Box>
          </Section>
        </Stack>
      </Container>
      <CashFlowDialog
        broker={broker}
        open={cashFlowOpen}
        onClose={() => setCashFlowOpen(false)}
        onError={setError}
        onComplete={(message) => {
          setNotice(message);
          load();
        }}
      />
    </Box>
  );
}
