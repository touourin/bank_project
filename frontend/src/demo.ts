/** Opt-in preview: sample data stays in memory and never reaches the API. */
export const isDemoMode =
  new URLSearchParams(window.location.search).get("demo") === "1";

export const demoStartStep =
  new URLSearchParams(window.location.search).get("step") === "4"
    ? "resolution"
    : "graphrag";
