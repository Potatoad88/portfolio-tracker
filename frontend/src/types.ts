export type Currency = "SGD" | "USD";
export type BrokerId = string;
export type View = "home" | BrokerId;

export type BrokerCapabilities = {
  contributions: boolean;
  performanceHistory: boolean;
  fundingHistory: boolean;
  cashFlowSync: boolean;
  exports: boolean;
};
export type BrokerMetadata = {
  id: BrokerId;
  displayName: string;
  configured: boolean;
  capabilities: BrokerCapabilities;
};
export type OverviewBroker = {
  broker: BrokerId;
  displayName: string;
  configured: boolean;
  hasData: boolean;
  totalEquity: string;
  allocationPct: string;
  lastSuccess: string | null;
  lastError: string | null;
  stale: boolean;
};
export type Overview = {
  complete: boolean;
  missingBrokers: BrokerId[];
  supportedBrokerCount: number;
  configuredBrokerCount: number;
  brokerCount: number;
  totalEquity: string;
  netContributions: string;
  overallPnl: string | null;
  pnlComplete: boolean;
  missingContributionBrokers: BrokerId[];
  cash: string;
  holdingsValue: string;
  stocksValue: string;
  fundsValue: string;
  otherHoldingsValue: string;
  brokers: OverviewBroker[];
};
export type Summary = {
  empty: boolean;
  supportsContributions: boolean;
  totalEquity?: string;
  netContributions?: string;
  overallPnl?: string;
  overallReturnPct?: string | null;
  cash?: string;
  holdingsValue?: string;
  unrealizedPnl?: string;
  reconciliationDifference?: string;
};
export type Position = {
  symbol: string;
  name: string;
  market: string;
  quantity: string;
  average_cost: string;
  market_price: string;
  market_value: string;
  unrealized_pnl: string;
  asset_type: string;
};
export type Funding = {
  transaction_id: string;
  type_label: string;
  amount: string;
  business_date: string;
  completed: number;
  original_currency: string;
  display_currency: string;
  direction: string;
  settlement_date: string | null;
  remark: string;
};
export type Point = {
  captured_at: string;
  total_equity: string;
  net_contributions: string;
  performance_value: string;
  funding_event: boolean;
};
export type Status = {
  lastSuccess: string | null;
  lastError: string | null;
  stale: boolean;
  components: Record<string, string>;
  cashFlowLastSuccess: string | null;
};
