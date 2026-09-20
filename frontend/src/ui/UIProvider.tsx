import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { App, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { ThemeContext, type Theme } from "../hooks/useTheme";
import { componentTheme, palettes } from "./theme";
import "./ui.css";

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const media = matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);
  return matches;
}

function savedTheme(): Theme {
  try {
    const value = localStorage.getItem("bank-studio-theme");
    if (value === "light" || value === "dark") return value;
  } catch {
    /* Storage is optional. */
  }
  return "system";
}

export function UIProvider({ children }: { children: ReactNode }) {
  const [preference, setTheme] = useState<Theme>(savedTheme);
  const systemDark = useMediaQuery("(prefers-color-scheme: dark)");
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const appearance =
    preference === "system" ? (systemDark ? "dark" : "light") : preference;
  const config = useMemo(
    () => componentTheme(appearance, reducedMotion),
    [appearance, reducedMotion],
  );
  useLayoutEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = appearance;
    for (const [key, value] of Object.entries(palettes[appearance])) {
      root.style.setProperty(`--${key}`, value);
    }
  }, [appearance]);
  useEffect(() => {
    try {
      localStorage.setItem("bank-studio-theme", preference);
    } catch {
      /* Theme remains available in memory. */
    }
  }, [preference]);
  return (
    <ThemeContext.Provider value={{ preference, appearance, setTheme }}>
      <ConfigProvider
        locale={zhCN}
        theme={config}
        button={{ autoInsertSpace: false }}
      >
        <App>{children}</App>
      </ConfigProvider>
    </ThemeContext.Provider>
  );
}
