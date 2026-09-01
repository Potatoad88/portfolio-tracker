import { useEffect, useState } from "react";
import { Box, CircularProgress } from "@mui/material";
import { api } from "./api";
import BrokerPage from "./pages/BrokerPage";
import HomePage from "./pages/HomePage";
import type { BrokerMetadata, Currency, Overview, View } from "./types";

const emptyOverview = (brokers: BrokerMetadata[]): Overview => ({
  complete: false,
  missingBrokers: brokers
    .filter((broker) => broker.configured)
    .map((broker) => broker.id),
  supportedBrokerCount: brokers.length,
  configuredBrokerCount: brokers.filter((broker) => broker.configured).length,
  brokerCount: 0,
  totalEquity: "0",
  netContributions: "0",
  overallPnl: "0",
  cash: "0",
  holdingsValue: "0",
  stocksValue: "0",
  fundsValue: "0",
  otherHoldingsValue: "0",
  brokers: brokers.map((broker) => ({
    broker: broker.id,
    displayName: broker.displayName,
    configured: broker.configured,
    hasData: false,
    totalEquity: "0",
    allocationPct: "0",
    lastSuccess: null,
    lastError: null,
    stale: true,
  })),
});

export default function App({
  dark,
  onToggleTheme,
}: {
  dark: boolean;
  onToggleTheme: () => void;
}) {
  const [view, setView] = useState<View>(
    () =>
      localStorage.getItem("portfolioView") ||
      localStorage.getItem("broker") ||
      "home",
  );
  const [currency, setCurrency] = useState<Currency>(() =>
    localStorage.getItem("reportingCurrency") === "USD" ? "USD" : "SGD",
  );
  const [brokers, setBrokers] = useState<BrokerMetadata[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    api<BrokerMetadata[]>("/api/brokers")
      .then((items) => {
        setBrokers(items);
        if (view !== "home" && !items.some((broker) => broker.id === view))
          setView("home");
        setError("");
      })
      .catch((reason) =>
        setError(
          reason instanceof Error ? reason.message : "Could not load brokers",
        ),
      )
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (view !== "home" || !brokers.length) return;
    setLoading(true);
    api<Overview>(`/api/overview?currency=${currency}`)
      .then((result) => {
        setOverview(result);
        setError("");
      })
      .catch((reason) =>
        setError(
          reason instanceof Error ? reason.message : "Could not load portfolio",
        ),
      )
      .finally(() => setLoading(false));
  }, [brokers, currency, view]);

  useEffect(() => localStorage.setItem("portfolioView", view), [view]);
  useEffect(
    () => localStorage.setItem("reportingCurrency", currency),
    [currency],
  );

  if (loading && !brokers.length)
    return (
      <Box sx={{ height: "100vh", display: "grid", placeItems: "center" }}>
        <CircularProgress aria-label="Loading portfolio" />
      </Box>
    );

  const selected = brokers.find((broker) => broker.id === view);
  if (view !== "home" && selected)
    return (
      <BrokerPage
        key={selected.id}
        brokers={brokers}
        metadata={selected}
        currency={currency}
        view={view}
        dark={dark}
        onCurrency={setCurrency}
        onNavigate={setView}
        onToggleTheme={onToggleTheme}
      />
    );

  return (
    <HomePage
      brokers={brokers}
      overview={overview || emptyOverview(brokers)}
      error={error}
      currency={currency}
      view="home"
      dark={dark}
      onCurrency={setCurrency}
      onNavigate={setView}
      onToggleTheme={onToggleTheme}
    />
  );
}
