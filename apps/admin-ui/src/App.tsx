import { ConfigProvider, App as AntApp } from "antd";
import { Route, Routes } from "react-router-dom";

import { useTheme } from "./theme/ThemeContext";
import { darkTheme, lightTheme } from "./theme/antdTheme";
import { Shell } from "./components/Shell";
import { CommandPaletteProvider } from "./components/CommandPalette";
import { AppRouter } from "./router";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import { Login } from "./pages/Login";

export default function App() {
  const { mode } = useTheme();
  const themeConfig = mode === "dark" ? darkTheme : lightTheme;

  return (
    <ConfigProvider theme={themeConfig} componentSize="middle">
      <AntApp>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/*"
            element={
              <ProtectedRoute>
                <CommandPaletteProvider>
                  <Shell>
                    <AppRouter />
                  </Shell>
                </CommandPaletteProvider>
              </ProtectedRoute>
            }
          />
        </Routes>
      </AntApp>
    </ConfigProvider>
  );
}
