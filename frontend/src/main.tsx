import React, { useMemo, useState } from "react";
import ReactDOM from "react-dom/client";
import { CssBaseline, ThemeProvider, createTheme } from "@mui/material";
import App from "./App";

function Root() {
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem("theme");
    return saved
      ? saved === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
  });
  const theme = useMemo(
    () =>
      createTheme({
        palette: {
          mode: dark ? "dark" : "light",
          primary: { main: dark ? "#ff9f45" : "#ed6c02" },
          success: { main: "#20a464" },
          error: { main: "#e5484d" },
          background: {
            default: dark ? "#0b0c0e" : "#f5f5f7",
            paper: dark ? "#15171a" : "#ffffff",
          },
        },
        shape: { borderRadius: 18 },
        typography: {
          fontFamily:
            'Inter, -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif',
          h4: { fontWeight: 750, letterSpacing: "-.035em" },
          h5: { fontWeight: 700, letterSpacing: "-.02em" },
          button: { textTransform: "none", fontWeight: 650 },
        },
        components: {
          MuiCard: { styleOverrides: { root: { backgroundImage: "none" } } },
          MuiPaper: { styleOverrides: { root: { backgroundImage: "none" } } },
          MuiButton: { defaultProps: { disableElevation: true } },
        },
      }),
    [dark],
  );
  const toggle = () =>
    setDark((value) => {
      localStorage.setItem("theme", !value ? "dark" : "light");
      return !value;
    });
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App dark={dark} onToggleTheme={toggle} />
    </ThemeProvider>
  );
}
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
