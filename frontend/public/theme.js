try {
  const preference = localStorage.getItem("bank-studio-theme") || "system";
  document.documentElement.dataset.theme =
    preference === "system"
      ? matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : preference;
} catch {
  document.documentElement.dataset.theme = "light";
}
