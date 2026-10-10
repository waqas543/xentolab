"use client";

import dynamic from "next/dynamic";
import { Spinner } from "../ui";

const placeholder = () => (
  <div className="grid h-full w-full place-items-center bg-surface-2">
    <Spinner />
  </div>
);

// Leaflet touches `window`, so the maps only render in the browser.
export const CompaniesMap = dynamic(() => import("./leaflet-maps").then((m) => m.CompaniesMap), {
  ssr: false,
  loading: placeholder,
});

export const MiniMap = dynamic(() => import("./leaflet-maps").then((m) => m.MiniMap), {
  ssr: false,
  loading: placeholder,
});

export type { Bounds } from "./leaflet-maps";
