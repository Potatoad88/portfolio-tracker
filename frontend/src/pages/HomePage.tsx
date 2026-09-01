import InfoOutlined from "@mui/icons-material/InfoOutlined";
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  Container,
  IconButton,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { money } from "../api";
import AppHeader from "../components/AppHeader";
import type { BrokerMetadata, Currency, Overview, View } from "../types";

export default function HomePage({
  brokers,
  overview,
  currency,
  view,
  dark,
  onCurrency,
  onNavigate,
  onToggleTheme,
  error,
}: {
  brokers: BrokerMetadata[];
  overview: Overview;
  currency: Currency;
  view: View;
  dark: boolean;
  onCurrency: (currency: Currency) => void;
  onNavigate: (view: View) => void;
  onToggleTheme: () => void;
  error: string;
}) {
  const assetRows = [
    {
      label: "Stocks & ETFs",
      value: overview.stocksValue,
      color: "primary.main",
    },
    {
      label: "Money market & funds",
      value: overview.fundsValue,
      color: "warning.main",
    },
    { label: "Cash", value: overview.cash, color: "success.main" },
    ...(Number(overview.otherHoldingsValue)
      ? [
          {
            label: "Other / unclassified",
            value: overview.otherHoldingsValue,
            color: "text.secondary",
          },
        ]
      : []),
  ];
  const assetTotal = assetRows.reduce((sum, row) => sum + Number(row.value), 0);
  const title = (id: string) =>
    overview.brokers.find((item) => item.broker === id)?.displayName || id;
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
      />
      <Container maxWidth="xl" sx={{ py: { xs: 3, md: 6 } }}>
        <Stack spacing={3}>
          {error && <Alert severity="error">{error}</Alert>}
          {!overview.complete && overview.brokerCount > 0 && (
            <Alert severity="info">
              Overall totals include available data only.{" "}
              {overview.missingBrokers.map(title).join(" and ")} has not synced
              yet.
            </Alert>
          )}
          <Box>
            <Typography variant="h4">Overall portfolio</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>
              Latest locally cached values · Reporting in {currency}
            </Typography>
          </Box>
          {overview.brokerCount === 0 ? (
            <Alert severity="info">
              No cached portfolio data yet. Open a configured broker to run the
              first sync.
            </Alert>
          ) : (
            <>
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
                {[
                  {
                    label: "Total equity",
                    value: money(overview.totalEquity, currency),
                  },
                  {
                    label: "Net contributions",
                    value: money(overview.netContributions, currency),
                  },
                  {
                    label: "Overall P&L",
                    value: `${Number(overview.overallPnl) >= 0 ? "+" : ""}${money(overview.overallPnl, currency)}`,
                    tone: Number(overview.overallPnl),
                    info: "Combined total equity − combined net contributions. Accuracy depends on complete deposit and withdrawal history for every represented broker.",
                  },
                  { label: "Cash", value: money(overview.cash, currency) },
                  {
                    label: "Holdings value",
                    value: money(overview.holdingsValue, currency),
                  },
                  {
                    label: "Brokers represented",
                    value: `${overview.brokerCount} cached · ${overview.configuredBrokerCount} configured`,
                  },
                ].map((card) => (
                  <Card key={card.label} variant="outlined">
                    <CardContent>
                      <Stack direction="row" alignItems="center">
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
                              aria-label="How overall P&L is calculated"
                            >
                              <InfoOutlined sx={{ fontSize: 17 }} />
                            </IconButton>
                          </Tooltip>
                        )}
                      </Stack>
                      <Typography
                        variant="h6"
                        sx={{
                          mt: 1,
                          fontWeight: 760,
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
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
                  gap: 3,
                }}
              >
                <Paper
                  variant="outlined"
                  sx={{ p: { xs: 2.5, md: 3 }, borderRadius: 3 }}
                >
                  <Typography variant="h6" sx={{ mb: 2 }}>
                    Broker allocation
                  </Typography>
                  <Stack spacing={2.5}>
                    {overview.brokers
                      .filter((item) => item.hasData)
                      .map((item) => (
                        <Box key={item.broker}>
                          <Stack direction="row" justifyContent="space-between">
                            <Typography fontWeight={650}>
                              {title(item.broker)}
                            </Typography>
                            <Typography>
                              {money(item.totalEquity, currency)} ·{" "}
                              {Number(item.allocationPct).toFixed(1)}%
                            </Typography>
                          </Stack>
                          <Box
                            sx={{
                              height: 8,
                              mt: 1,
                              borderRadius: 8,
                              bgcolor: "action.hover",
                              overflow: "hidden",
                            }}
                          >
                            <Box
                              sx={{
                                height: "100%",
                                width: `${item.allocationPct}%`,
                                bgcolor:
                                  overview.brokers.indexOf(item) % 2 === 0
                                    ? "primary.main"
                                    : "warning.main",
                              }}
                            />
                          </Box>
                        </Box>
                      ))}
                  </Stack>
                </Paper>
                <Paper
                  variant="outlined"
                  sx={{ p: { xs: 2.5, md: 3 }, borderRadius: 3 }}
                >
                  <Typography variant="h6" sx={{ mb: 2 }}>
                    Asset allocation
                  </Typography>
                  <Stack spacing={2.5}>
                    {assetRows
                      .filter((row) => Number(row.value))
                      .map((row) => {
                        const percentage = assetTotal
                          ? (Number(row.value) / assetTotal) * 100
                          : 0;
                        return (
                          <Box key={row.label}>
                            <Stack
                              direction="row"
                              justifyContent="space-between"
                            >
                              <Typography fontWeight={650}>
                                {row.label}
                              </Typography>
                              <Typography>
                                {money(row.value, currency)} ·{" "}
                                {percentage.toFixed(1)}%
                              </Typography>
                            </Stack>
                            <Box
                              sx={{
                                height: 8,
                                mt: 1,
                                borderRadius: 8,
                                bgcolor: "action.hover",
                                overflow: "hidden",
                              }}
                            >
                              <Box
                                sx={{
                                  height: "100%",
                                  width: `${percentage}%`,
                                  bgcolor: row.color,
                                }}
                              />
                            </Box>
                          </Box>
                        );
                      })}
                  </Stack>
                </Paper>
              </Box>
            </>
          )}
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
              gap: 2,
            }}
          >
            {overview.brokers.map((item) => (
              <Card
                key={item.broker}
                variant="outlined"
                onClick={() => onNavigate(item.broker)}
                sx={{
                  cursor: "pointer",
                  transition: "transform .2s",
                  "&:hover": { transform: "translateY(-2px)" },
                }}
              >
                <CardContent>
                  <Stack
                    direction="row"
                    justifyContent="space-between"
                    alignItems="center"
                  >
                    <Typography variant="h6">{title(item.broker)}</Typography>
                    <Chip
                      size="small"
                      label={
                        !item.configured
                          ? "Not configured"
                          : !item.hasData
                            ? "Not synced"
                            : item.lastError
                              ? "Error"
                              : item.stale
                                ? "Stale"
                                : "Current"
                      }
                      color={
                        !item.configured || !item.hasData
                          ? "default"
                          : item.lastError
                            ? "error"
                            : item.stale
                              ? "warning"
                              : "success"
                      }
                      variant="outlined"
                    />
                  </Stack>
                  <Typography variant="h5" sx={{ mt: 2, fontWeight: 760 }}>
                    {item.hasData
                      ? money(item.totalEquity, currency)
                      : item.configured
                        ? "No cached data"
                        : "Configuration required"}
                  </Typography>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mt: 1 }}
                  >
                    Last sync:{" "}
                    {item.lastSuccess
                      ? new Date(item.lastSuccess).toLocaleString()
                      : "never"}
                  </Typography>
                  {item.lastError && (
                    <Typography variant="body2" color="error" sx={{ mt: 1 }}>
                      {item.lastError}
                    </Typography>
                  )}
                </CardContent>
              </Card>
            ))}
          </Box>
        </Stack>
      </Container>
    </Box>
  );
}
