"use client";

import dynamic from "next/dynamic";
import { Spinner } from "../ui";

export const DensityMap = dynamic(() => import("./density-map-leaflet").then((m) => m.DensityMap), {
  ssr: false,
  loading: () => <div className="grid h-full w-full place-items-center bg-surface-2"><Spinner /></div>,
});
export type { GridCell, View } from "./density-map-leaflet";
