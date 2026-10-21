// Route for the "Build it by hand" page: #build-by-hand is a real history entry, so the browser's Back button
// returns to the demo. Kept apart from the page so the page (and its snippet data) can load lazily.
import { useEffect, useState } from "react";

const HASH = "#build-by-hand";

export function openBuildByHand() {
  if (location.hash === HASH) return;
  history.pushState({ bbh: 1 }, "", HASH);
  window.dispatchEvent(new PopStateEvent("popstate", { state: history.state }));
}

export function closeBuildByHand() {
  if (history.state?.bbh) history.back();
  else {
    history.replaceState(null, "", location.pathname + location.search);
    window.dispatchEvent(new PopStateEvent("popstate", { state: null }));
  }
}

/** True while the page is open (follows Back / Forward and links to #build-by-hand). */
export function useBuildByHandOpen() {
  const [open, setOpen] = useState(() => location.hash === HASH);
  useEffect(() => {
    const sync = () => setOpen(location.hash === HASH);
    window.addEventListener("popstate", sync);
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("hashchange", sync);
    };
  }, []);
  return open;
}
