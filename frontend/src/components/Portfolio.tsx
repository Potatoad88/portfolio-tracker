import { PointerEvent as ReactPointerEvent, ReactNode, useState } from "react";
import ExpandMore from "@mui/icons-material/ExpandMore";
import {
  Box,
  Button,
  Collapse,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { money } from "../api";
import type { Currency, Point, Position } from "../types";

export function LineChart({
  data,
  currency,
  metric,
}: {
  data: Point[];
  currency: Currency;
  metric: "total_equity" | "performance_value";
}) {
  const [active, setActive] = useState<number | null>(null);
  if (!data.length)
    return (
      <Box
        sx={{
          height: 220,
          display: "grid",
          placeItems: "center",
          color: "text.secondary",
        }}
      >
        History will appear after a refresh.
      </Box>
    );
  const width = 800,
    height = 270,
    left = 64,
    right = 18,
    top = 20,
    bottom = 38,
    plotWidth = width - left - right,
    plotHeight = height - top - bottom;
  const values = data.map((p) => Number(p[metric])),
    rawMin = Math.min(...values),
    rawMax = Math.max(...values),
    padding = (rawMax - rawMin || Math.max(Math.abs(rawMax) * 0.05, 1)) * 0.12,
    min = rawMin - padding,
    max = rawMax + padding,
    range = max - min;
  const coords = values.map((value, index) => ({
    x: left + (index / (values.length - 1 || 1)) * plotWidth,
    y: top + (1 - (value - min) / range) * plotHeight,
  }));
  const points = coords.map((p) => `${p.x},${p.y}`).join(" "),
    latest = coords.at(-1)!,
    trendUp = values.at(-1)! >= values[0];
  const compact = (value: number) =>
    new Intl.NumberFormat("en-SG", {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(value);
  const date = (value: string) =>
    new Date(value).toLocaleDateString(undefined, {
      day: "numeric",
      month: "short",
      year: "2-digit",
    });
  const inspect = (event: ReactPointerEvent<SVGSVGElement>) => {
    const rect = event.currentTarget.getBoundingClientRect(),
      x = ((event.clientX - rect.left) / rect.width) * width;
    setActive(
      Math.max(
        0,
        Math.min(
          data.length - 1,
          Math.round(((x - left) / plotWidth) * (data.length - 1 || 1)),
        ),
      ),
    );
  };
  const selected =
    active == null ? null : { ...coords[active], ...data[active] };
  const tooltipX = selected
      ? selected.x > width - 190
        ? selected.x - 158
        : selected.x + 12
      : 0,
    tooltipY = selected ? Math.max(8, selected.y - 66) : 0;
  return (
    <Box
      component="svg"
      role="img"
      aria-label="Portfolio equity history"
      viewBox={`0 0 ${width} ${height}`}
      onPointerMove={inspect}
      onPointerLeave={() => setActive(null)}
      sx={{
        width: "100%",
        height: "auto",
        minHeight: 230,
        color: trendUp ? "success.main" : "error.main",
        touchAction: "pan-y",
      }}
    >
      <title>
        {metric === "total_equity"
          ? "Portfolio equity history"
          : "Contribution-adjusted performance history"}
      </title>
      <desc>{`Value moved from ${money(String(values[0]), currency)} to ${money(String(values.at(-1)), currency)} across ${data.length} recorded points.`}</desc>
      <defs>
        <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="currentColor" stopOpacity=".24" />
          <stop offset="1" stopColor="currentColor" stopOpacity=".015" />
        </linearGradient>
      </defs>
      {[0, 0.25, 0.5, 0.75, 1].map((f) => {
        const y = top + f * plotHeight,
          value = max - f * range;
        return (
          <g key={f}>
            <line
              x1={left}
              x2={width - right}
              y1={y}
              y2={y}
              stroke="currentColor"
              opacity=".1"
              strokeDasharray="4 5"
            />
            <text
              x={left - 10}
              y={y + 4}
              textAnchor="end"
              fill="currentColor"
              opacity=".65"
              fontSize="11"
            >
              {compact(value)}
            </text>
          </g>
        );
      })}
      <polygon
        points={`${left},${height - bottom} ${points} ${latest.x},${height - bottom}`}
        fill="url(#fill)"
      />
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {data.map((point, i) =>
        point.funding_event ? (
          <rect
            key={`fund-${i}`}
            x={coords[i].x - 3.5}
            y={coords[i].y - 3.5}
            width="7"
            height="7"
            rx="1"
            fill="#ed6c02"
            stroke="white"
            strokeWidth="1.5"
          />
        ) : null,
      )}
      {[0, Math.floor((data.length - 1) / 2), data.length - 1]
        .filter((v, i, a) => a.indexOf(v) === i)
        .map((i) => (
          <text
            key={i}
            x={coords[i].x}
            y={height - 10}
            textAnchor={
              i === 0 ? "start" : i === data.length - 1 ? "end" : "middle"
            }
            fill="currentColor"
            opacity=".65"
            fontSize="11"
          >
            {date(data[i].captured_at)}
          </text>
        ))}
      <circle
        cx={latest.x}
        cy={latest.y}
        r="5"
        fill="currentColor"
        stroke="white"
        strokeWidth="3"
      />
      {selected && (
        <g pointerEvents="none">
          <line
            x1={selected.x}
            x2={selected.x}
            y1={top}
            y2={height - bottom}
            stroke="currentColor"
            opacity=".3"
            strokeDasharray="3 4"
          />
          <circle
            cx={selected.x}
            cy={selected.y}
            r="6"
            fill="currentColor"
            stroke="white"
            strokeWidth="2"
          />
          <rect
            x={tooltipX}
            y={tooltipY}
            width="146"
            height="52"
            rx="10"
            fill="currentColor"
          />
          <text
            x={tooltipX + 12}
            y={tooltipY + 20}
            fill="white"
            fontSize="11"
            opacity=".85"
          >
            {date(selected.captured_at)}
          </text>
          <text
            x={tooltipX + 12}
            y={tooltipY + 40}
            fill="white"
            fontSize="15"
            fontWeight="700"
          >
            {money(selected[metric], currency)}
          </text>
        </g>
      )}
    </Box>
  );
}

export function Section({
  title,
  subtitle,
  children,
  initial = true,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  initial?: boolean;
}) {
  const [open, setOpen] = useState(initial);
  return (
    <Paper
      variant="outlined"
      sx={{
        overflow: "hidden",
        borderColor: "divider",
        boxShadow: "0 8px 30px rgba(0,0,0,.035)",
      }}
    >
      <Button
        fullWidth
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        sx={{
          p: { xs: 2, md: 2.5 },
          justifyContent: "flex-start",
          color: "text.primary",
          borderRadius: 0,
        }}
      >
        <Box sx={{ flexGrow: 1, textAlign: "left" }}>
          <Typography variant="h5">{title}</Typography>
          {subtitle && (
            <Typography variant="body2" color="text.secondary">
              {subtitle}
            </Typography>
          )}
        </Box>
        <ExpandMore
          sx={{
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform .2s",
          }}
        />
      </Button>
      <Collapse in={open}>
        <Box sx={{ borderTop: 1, borderColor: "divider" }}>{children}</Box>
      </Collapse>
    </Paper>
  );
}

export function HoldingsTable({
  rows,
  currency,
  empty,
  total,
}: {
  rows: Position[];
  currency: Currency;
  empty: string;
  total: number;
}) {
  const signed = (value: string) =>
    `${Number(value) >= 0 ? "+" : ""}${money(value, currency)}`;
  return (
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            {[
              "Holding",
              "Market",
              "Quantity",
              "Avg cost",
              "Price",
              "Market value",
              "Allocation",
              "Unrealized P&L",
            ].map((x) => (
              <TableCell
                key={x}
                align={x === "Holding" ? "left" : "right"}
                sx={{ color: "text.secondary", fontWeight: 650 }}
              >
                {x}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.length ? (
            rows.map((p) => {
              const gain = Number(p.unrealized_pnl) >= 0;
              return (
                <TableRow key={`${p.asset_type}-${p.market}-${p.symbol}`} hover>
                  <TableCell>
                    <Typography fontWeight={700}>{p.symbol}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {p.name}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">{p.market}</TableCell>
                  <TableCell align="right">{p.quantity}</TableCell>
                  <TableCell align="right">
                    {money(p.average_cost, currency)}
                  </TableCell>
                  <TableCell align="right">
                    {money(p.market_price, currency)}
                  </TableCell>
                  <TableCell align="right" sx={{ fontWeight: 650 }}>
                    {money(p.market_value, currency)}
                  </TableCell>
                  <TableCell align="right">
                    {total
                      ? `${((Number(p.market_value) / total) * 100).toFixed(1)}%`
                      : "—"}
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{
                      fontWeight: 750,
                      color: gain ? "success.main" : "error.main",
                    }}
                  >
                    {signed(p.unrealized_pnl)}
                  </TableCell>
                </TableRow>
              );
            })
          ) : (
            <TableRow>
              <TableCell
                colSpan={8}
                align="center"
                sx={{ py: 5, color: "text.secondary" }}
              >
                {empty}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
