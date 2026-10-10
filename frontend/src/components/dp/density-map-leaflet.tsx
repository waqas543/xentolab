"use client";

import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";
import { MapContainer, TileLayer, useMap, useMapEvents } from "react-leaflet";
import { formatNumber } from "@/lib/format";
import { useResolvedTheme } from "../theme-toggle";

export interface GridCell { latitude: number; longitude: number; count: number }
export interface View { west: number; south: number; east: number; north: number; zoom: number }

function Cells({ cells }: { cells: GridCell[] }) {
  const map = useMap();
  useEffect(() => {
    const color = getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim() || "#2a78d6";
    const max = Math.max(1, ...cells.map((c) => c.count));
    const layer = L.layerGroup();
    for (const cell of cells) {
      const share = Math.sqrt(cell.count / max);
      L.circleMarker([cell.latitude, cell.longitude], {
        radius: 3 + share * 22,
        color,
        weight: 1,
        fillColor: color,
        fillOpacity: 0.15 + share * 0.55,
      }).bindTooltip(`${formatNumber(cell.count)} businesses`).addTo(layer);
    }
    layer.addTo(map);
    return () => {
      layer.remove();
    };
  }, [map, cells]);
  return null;
}

function Watcher({ onView }: { onView: (view: View) => void }) {
  const emit = (map: L.Map) => {
    const b = map.getBounds();
    onView({ west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth(), zoom: map.getZoom() });
  };
  const map = useMapEvents({ moveend: () => emit(map) });
  const ref = useRef(false);
  useEffect(() => {
    if (!ref.current) {
      ref.current = true;
      emit(map);
    }
  });
  return null;
}

export function DensityMap({ cells, onView }: { cells: GridCell[]; onView: (view: View) => void }) {
  const theme = useResolvedTheme();
  return (
    <MapContainer center={[30.2, 69.3]} zoom={5} className="h-full w-full" preferCanvas>
      <TileLayer key={theme} url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        className={theme === "dark" ? "map-tiles-dark" : "map-tiles-light"} maxZoom={19} />
      <Cells cells={cells} />
      <Watcher onView={onView} />
    </MapContainer>
  );
}
