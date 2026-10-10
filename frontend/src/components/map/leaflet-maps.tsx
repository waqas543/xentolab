"use client";

import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";
import { MapContainer, TileLayer, useMap, useMapEvents } from "react-leaflet";
import { humanize } from "@/lib/format";
import type { CompanyRef, MapPoint } from "@/lib/types";
import { useResolvedTheme } from "../theme-toggle";

// Pakistan, roughly.
const DEFAULT_CENTER: L.LatLngTuple = [30.2, 69.3];
const DEFAULT_ZOOM = 5;

export interface Bounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

function Basemap() {
  const theme = useResolvedTheme();
  return (
    <TileLayer
      key={theme}
      url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      className={theme === "dark" ? "map-tiles-dark" : "map-tiles-light"}
      maxZoom={19}
    />
  );
}

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#2a78d6";
}

function escapeHtml(text: string) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/** Draws thousands of points on a canvas layer (much faster than DOM markers). */
function PointsLayer({ points, onOpen }: { points: MapPoint[]; onOpen: (ref: CompanyRef) => void }) {
  const map = useMap();
  const theme = useResolvedTheme();
  const onOpenRef = useRef(onOpen);
  useEffect(() => {
    onOpenRef.current = onOpen;
  }, [onOpen]);

  useEffect(() => {
    const renderer = L.canvas({ padding: 0.5, tolerance: 6 });
    const color = cssVar("--series-1");
    const ring = cssVar("--surface");
    const layer = L.layerGroup();

    for (const [type, id, lat, lon, name, group, category] of points) {
      const marker = L.circleMarker([lat, lon], {
        renderer,
        radius: 4.5,
        color: ring,
        weight: 1.5,
        fillColor: color,
        fillOpacity: 0.85,
      });
      marker.bindPopup(() => {
        const container = document.createElement("div");
        container.innerHTML = `
          <div style="min-width:180px">
            <div style="font-weight:600;font-size:13px">${escapeHtml(name)}</div>
            <div style="color:var(--ink-2);font-size:12px;margin-top:2px">${escapeHtml(humanize(category))} · ${escapeHtml(group)}</div>
            <button style="margin-top:8px;font-size:12px;color:var(--accent-ink);font-weight:500">View details →</button>
          </div>`;
        container.querySelector("button")!.addEventListener("click", () => onOpenRef.current({ osm_type: type, osm_id: id }));
        return container;
      });
      marker.addTo(layer);
    }

    layer.addTo(map);
    return () => {
      layer.remove();
    };
  }, [map, points, theme]);

  return null;
}

function BoundsWatcher({ onChange }: { onChange: (bounds: Bounds, zoom: number) => void }) {
  const emit = (map: L.Map) => {
    const b = map.getBounds();
    onChange({ west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() }, map.getZoom());
  };
  const map = useMapEvents({ moveend: () => emit(map) });
  useEffect(() => emit(map), []); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

/** Fly to `target` whenever it changes (a new object = a new request to fit). */
function FitTo({ target }: { target: Bounds | null }) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    const pad = 0.005;
    map.flyToBounds(
      [
        [target.south - pad, target.west - pad],
        [target.north + pad, target.east + pad],
      ],
      { maxZoom: 16, duration: 0.6, padding: [24, 24] },
    );
  }, [map, target]);
  return null;
}

export function CompaniesMap({
  points,
  fitTo,
  onBoundsChange,
  onOpen,
}: {
  points: MapPoint[];
  /** Each entry flies the map when it changes. */
  fitTo: (Bounds | null)[];
  onBoundsChange: (bounds: Bounds, zoom: number) => void;
  onOpen: (ref: CompanyRef) => void;
}) {
  return (
    <MapContainer center={DEFAULT_CENTER} zoom={DEFAULT_ZOOM} className="h-full w-full" preferCanvas>
      <Basemap />
      <PointsLayer points={points} onOpen={onOpen} />
      <BoundsWatcher onChange={onBoundsChange} />
      {fitTo.map((target, i) => (
        <FitTo key={i} target={target} />
      ))}
    </MapContainer>
  );
}

function Recenter({ position }: { position: L.LatLngTuple }) {
  const map = useMap();
  useEffect(() => {
    map.setView(position, map.getZoom());
  }, [map, position]);
  return null;
}

function Pin({ position }: { position: L.LatLngTuple }) {
  const map = useMap();
  useEffect(() => {
    const marker = L.circleMarker(position, {
      radius: 7,
      color: cssVar("--surface"),
      weight: 2,
      fillColor: cssVar("--series-1"),
      fillOpacity: 1,
    }).addTo(map);
    return () => {
      marker.remove();
    };
  }, [map, position]);
  return null;
}

export function MiniMap({ lat, lon }: { lat: number; lon: number }) {
  const position: L.LatLngTuple = [lat, lon];
  return (
    <MapContainer center={position} zoom={16} className="h-full w-full" scrollWheelZoom={false} attributionControl={false}>
      <Basemap />
      <Pin position={position} />
      <Recenter position={position} />
    </MapContainer>
  );
}
