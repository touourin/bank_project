/** Opt-in preview: sample data stays in memory and never reaches the API. */
export const isDemoMode =
  new URLSearchParams(window.location.search).get("demo") === "1";

const params = new URLSearchParams(window.location.search);
const workspace = params.get("workspace");
// Existing shared demo links retain their original destination.
export const demoStartStep =
  workspace === "conversion" ||
  workspace === "resolution" ||
  workspace === "analysis"
    ? workspace
    : params.get("step") === "4"
      ? "resolution"
      : "analysis";
