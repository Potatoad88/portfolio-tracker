import { useState } from "react";
import type { Dayjs } from "dayjs";
import { DatePicker } from "@mui/x-date-pickers/DatePicker";
import { LocalizationProvider } from "@mui/x-date-pickers/LocalizationProvider";
import { AdapterDayjs } from "@mui/x-date-pickers/AdapterDayjs";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Typography,
} from "@mui/material";
import { api } from "../api";

type Props = {
  broker: string;
  open: boolean;
  rangeSync: boolean;
  coverageStart: string | null;
  onClose: () => void;
  onComplete: (message: string) => void;
  onError: (message: string) => void;
};

export default function CashFlowDialog({
  broker,
  open,
  rangeSync,
  coverageStart,
  onClose,
  onComplete,
  onError,
}: Props) {
  const [date, setDate] = useState<Dayjs | null>(null);
  const [dates, setDates] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const initialRangeSync = rangeSync && !coverageStart;

  const add = () => {
    if (!date) return;
    const value = date.format("YYYY-MM-DD");
    if (dates.includes(value))
      return onError("That cash-flow date is already selected");
    if (dates.length >= 20)
      return onError("Select no more than 20 cash-flow dates");
    setDates([...dates, value].sort());
    setDate(null);
  };

  const fetchCashFlow = async () => {
    setLoading(true);
    try {
      const body = rangeSync
        ? {
            startDate: initialRangeSync
              ? date?.format("YYYY-MM-DD")
              : undefined,
          }
        : { dates };
      const result = await api<{ rawCount: number; verifiedCount: number }>(
        `/api/cash-flow/sync?broker=${broker}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      const message = `Fetched ${result.rawCount} record${result.rawCount === 1 ? "" : "s"}; ${result.verifiedCount} verified transfer${result.verifiedCount === 1 ? "" : "s"} shown.`;
      setDate(null);
      setDates([]);
      onClose();
      onComplete(message);
    } catch (error) {
      onError(error instanceof Error ? error.message : "Cash-flow sync failed");
    } finally {
      setLoading(false);
    }
  };

  const picker = (
    <LocalizationProvider dateAdapter={AdapterDayjs}>
      <DatePicker
        label={rangeSync ? "First funding date or earlier" : "Cash-flow date"}
        value={date}
        onChange={setDate}
        disableFuture
        format="DD/MM/YYYY"
        shouldDisableDate={(value) =>
          !rangeSync && dates.includes(value.format("YYYY-MM-DD"))
        }
        slotProps={{ textField: { fullWidth: true } }}
      />
    </LocalizationProvider>
  );

  return (
    <Dialog
      open={open}
      onClose={() => !loading && onClose()}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle>Fetch cash flow</DialogTitle>
      <DialogContent>
        {rangeSync ? (
          <>
            <Typography color="text.secondary" sx={{ mb: 2 }}>
              {initialRangeSync
                ? "Choose a date on or before your first IBKR deposit. The initial import is fetched in safe one-year ranges and stored locally."
                : `Contribution history is complete from ${new Date(`${coverageStart}T00:00:00`).toLocaleDateString()}. Fetching again checks recent changes with a two-day overlap.`}
            </Typography>
            {initialRangeSync && picker}
          </>
        ) : (
          <>
            <Typography color="text.secondary" sx={{ mb: 2 }}>
              Add up to 20 known deposit or withdrawal dates. The broker is
              queried once per date.
            </Typography>
            <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
              {picker}
              <Button onClick={add} disabled={!date || dates.length >= 20}>
                Add
              </Button>
            </Stack>
            <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", mt: 2 }}>
              {dates.map((value) => (
                <Chip
                  key={value}
                  label={new Date(`${value}T00:00:00`).toLocaleDateString()}
                  onDelete={() =>
                    setDates(dates.filter((item) => item !== value))
                  }
                  disabled={loading}
                />
              ))}
            </Box>
            <Typography
              variant="caption"
              color="text.secondary"
              display="block"
              sx={{ mt: 2 }}
            >
              {dates.length}/20 dates selected
            </Typography>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={fetchCashFlow}
          disabled={
            loading ||
            (initialRangeSync && !date) ||
            (!rangeSync && !dates.length)
          }
        >
          {loading ? (
            <CircularProgress size={18} color="inherit" />
          ) : (
            "Fetch cash flow"
          )}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
