import { ConfigProvider, App as AntApp } from "antd";
import { useTheme } from "./theme/ThemeContext";
import { darkTheme, lightTheme } from "./theme/antdTheme";
import { Shell } from "./components/Shell";
import { CommandPaletteProvider } from "./components/CommandPalette";
import { AppRouter } from "./router";

export default function App() {
  const { mode } = useTheme();
  const themeConfig = mode === "dark" ? darkTheme : lightTheme;

  return (
    <ConfigProvider theme={themeConfig} componentSize="middle">
      <AntApp>
        <CommandPaletteProvider>
          <Shell>
            <AppRouter />
          </Shell>
        </CommandPaletteProvider>
      </AntApp>
    </ConfigProvider>
  );
}
