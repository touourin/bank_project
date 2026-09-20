import { theme, type ThemeConfig } from "antd";

export type Appearance = "light" | "dark";

export const palettes = {
  light: {
    canvas: "#f6f7f4",
    surface: "#ffffff",
    sidebar: "#fafbf8",
    text: "#25312d",
    muted: "#7d8982",
    subtle: "#a2aaa6",
    border: "#e4e9e3",
    hover: "#f0f3ee",
    accent: "#287561",
    "accent-hover": "#1b5c4c",
    "on-accent": "#ffffff",
    "accent-soft": "#eaf4ee",
    focus: "#91cbb7",
    hero: "#eff4ee",
    "hero-border": "#dfe7dc",
    blue: "#507b9e",
    "blue-soft": "#eef3f8",
    amber: "#9c7b3a",
    "amber-soft": "#f8f3e7",
    red: "#b85750",
    "red-soft": "#fcf0ee",
    "red-border": "#f0d2ce",
    shadow: "0 25px 90px #15281f22",
  },
  dark: {
    canvas: "#111716",
    surface: "#19211f",
    sidebar: "#151c1a",
    text: "#e1e9e4",
    muted: "#91a098",
    subtle: "#697c72",
    border: "#2a3630",
    hover: "#23312a",
    accent: "#7fc5a6",
    "accent-hover": "#a3ddbf",
    "on-accent": "#142c21",
    "accent-soft": "#233c30",
    focus: "#528c73",
    hero: "#1e2d24",
    "hero-border": "#314636",
    blue: "#91b6d2",
    "blue-soft": "#233440",
    amber: "#d7b877",
    "amber-soft": "#393222",
    red: "#e1a29a",
    "red-soft": "#3a2927",
    "red-border": "#604139",
    shadow: "0 25px 90px #00000077",
  },
} as const;

export function componentTheme(
  appearance: Appearance,
  reducedMotion: boolean,
): ThemeConfig {
  const colors = palettes[appearance];
  return {
    algorithm:
      appearance === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: colors.accent,
      colorInfo: colors.blue,
      colorSuccess: colors.accent,
      colorSuccessBg: colors["accent-soft"],
      colorSuccessBorder: colors["hero-border"],
      colorWarning: colors.amber,
      colorWarningBg: colors["amber-soft"],
      colorError: colors.red,
      colorErrorBg: colors["red-soft"],
      colorErrorBorder: colors["red-border"],
      colorInfoBg: colors["blue-soft"],
      colorBgBase: colors.surface,
      colorBgContainer: colors.surface,
      colorBgElevated: colors.surface,
      colorBgLayout: colors.canvas,
      colorText: colors.text,
      colorTextSecondary: colors.muted,
      colorBorder: colors.border,
      colorBorderSecondary: colors.border,
      borderRadius: 8,
      controlHeight: 36,
      fontFamily:
        'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      fontSize: 13,
      motion: !reducedMotion,
    },
    components: {
      Button: { primaryColor: colors["on-accent"] },
      Table: {
        headerBg: colors.canvas,
        rowHoverBg: colors.hover,
        cellPaddingBlockSM: 12,
        cellPaddingInlineSM: 16,
      },
      Tabs: { horizontalMargin: "0 0 20px 0" },
    },
  };
}
