"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

type Theme = "system" | "light" | "dark";
const ORDER: Theme[] = ["system", "light", "dark"];
const ICONS = { system: Monitor, light: Sun, dark: Moon };

// The <html data-theme> attribute is the source of truth (set before paint in layout.tsx).
function subscribe(onChange: () => void) {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  media.addEventListener("change", onChange);
  return () => {
    observer.disconnect();
    media.removeEventListener("change", onChange);
  };
}

function chosenTheme(): Theme {
  const attr = document.documentElement.dataset.theme;
  return attr === "light" || attr === "dark" ? attr : "system";
}

function resolvedTheme(): "light" | "dark" {
  const chosen = chosenTheme();
  if (chosen !== "system") return chosen;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useResolvedTheme(): "light" | "dark" {
  return useSyncExternalStore(subscribe, resolvedTheme, () => "light");
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, chosenTheme, () => "system" as Theme);

  const cycle = () => {
    const next = ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length];
    try {
      if (next === "system") localStorage.removeItem("theme");
      else localStorage.setItem("theme", next);
    } catch {}
    if (next === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = next;
  };

  const Icon = ICONS[theme];
  return (
    <button
      onClick={cycle}
      title={`Theme: ${theme}`}
      aria-label={`Theme: ${theme}. Click to change`}
      className="rounded-md p-1.5 text-ink-2 hover:bg-surface-hover hover:text-ink"
    >
      <Icon size={16} />
    </button>
  );
}
