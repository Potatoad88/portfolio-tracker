import type { ReactNode } from "react";
import DarkModeOutlined from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlined from "@mui/icons-material/LightModeOutlined";
import {
  AppBar,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Tab,
  Tabs,
  Toolbar,
  Tooltip,
  Typography,
} from "@mui/material";
import type { BrokerMetadata, Currency, View } from "../types";

export default function AppHeader({
  brokers,
  view,
  currency,
  dark,
  actions,
  onView,
  onCurrency,
  onToggleTheme,
}: {
  brokers: BrokerMetadata[];
  view: View;
  currency: Currency;
  dark: boolean;
  actions?: ReactNode;
  onView: (view: View) => void;
  onCurrency: (currency: Currency) => void;
  onToggleTheme: () => void;
}) {
  return (
    <AppBar
      position="sticky"
      color="transparent"
      elevation={0}
      sx={{
        backdropFilter: "blur(20px)",
        backgroundColor: dark ? "rgba(11,12,14,.78)" : "rgba(255,255,255,.78)",
        borderBottom: 1,
        borderColor: "divider",
      }}
    >
      <Toolbar sx={{ gap: 1.25, maxWidth: 1536, width: "100%", mx: "auto" }}>
        <Typography
          variant="h6"
          sx={{ fontWeight: 800, letterSpacing: "-.03em" }}
        >
          Portfolio Tracker
        </Typography>
        <Tabs
          value={view}
          onChange={(_, value) => onView(value)}
          sx={{ flexGrow: 1, minHeight: 48 }}
        >
          <Tab value="home" label="Home" />
          {brokers.map((broker) => (
            <Tab key={broker.id} value={broker.id} label={broker.displayName} />
          ))}
        </Tabs>
        <FormControl size="small">
          <InputLabel id="currency-label">Currency</InputLabel>
          <Select
            labelId="currency-label"
            value={currency}
            label="Currency"
            onChange={(event) => onCurrency(event.target.value as Currency)}
          >
            <MenuItem value="SGD">SGD</MenuItem>
            <MenuItem value="USD">USD</MenuItem>
          </Select>
        </FormControl>
        <Tooltip title={dark ? "Use light mode" : "Use dark mode"}>
          <IconButton
            onClick={onToggleTheme}
            aria-label={dark ? "Use light mode" : "Use dark mode"}
          >
            {dark ? <LightModeOutlined /> : <DarkModeOutlined />}
          </IconButton>
        </Tooltip>
        {actions}
      </Toolbar>
    </AppBar>
  );
}
