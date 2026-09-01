import {
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { money } from "../api";
import type { Currency, Funding } from "../types";

export default function FundingTable({
  funding,
  currency,
  cashFlowSync,
}: {
  funding: Funding[];
  currency: Currency;
  cashFlowSync: boolean;
}) {
  const columns = cashFlowSync
    ? ["Date", "Type", "Direction", "Reference", "Amount"]
    : ["Date", "Type", "Transaction ID", "Status", "Amount"];
  return (
    <TableContainer>
      <Table size="small">
        <TableHead>
          <TableRow>
            {columns.map((column, index) => (
              <TableCell
                key={column}
                align={index === columns.length - 1 ? "right" : "left"}
                sx={{ color: "text.secondary", fontWeight: 650 }}
              >
                {column}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {funding.length ? (
            funding.map((item) => (
              <TableRow key={item.transaction_id} hover>
                <TableCell>
                  {new Date(
                    `${item.business_date}T00:00:00`,
                  ).toLocaleDateString()}
                </TableCell>
                <TableCell sx={{ fontWeight: 650 }}>
                  {item.type_label}
                </TableCell>
                {cashFlowSync ? (
                  <>
                    <TableCell>{item.direction || "—"}</TableCell>
                    <TableCell sx={{ maxWidth: 420, whiteSpace: "normal" }}>
                      {item.remark || "—"}
                    </TableCell>
                  </>
                ) : (
                  <>
                    <TableCell>{item.transaction_id}</TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={item.completed ? "Completed" : "Pending"}
                        color={item.completed ? "success" : "default"}
                        variant="outlined"
                      />
                    </TableCell>
                  </>
                )}
                <TableCell align="right" sx={{ fontWeight: 650 }}>
                  {money(item.amount, item.display_currency)}
                  {!cashFlowSync && currency !== item.original_currency && (
                    <Typography
                      variant="caption"
                      display="block"
                      color="text.secondary"
                    >
                      Originally {item.original_currency}
                    </Typography>
                  )}
                </TableCell>
              </TableRow>
            ))
          ) : (
            <TableRow>
              <TableCell colSpan={5} align="center" sx={{ py: 5 }}>
                No verified{" "}
                {cashFlowSync
                  ? "deposits or withdrawals"
                  : "funding transactions"}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
