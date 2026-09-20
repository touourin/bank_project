import { createContext, useContext } from "react";
import type { Appearance } from "../ui/theme";

export type Theme = Appearance | "system";
export const ThemeContext = createContext<{
  preference: Theme;
  appearance: Appearance;
  setTheme: (theme: Theme) => void;
} | null>(null);

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside UIProvider");
  return context;
}
