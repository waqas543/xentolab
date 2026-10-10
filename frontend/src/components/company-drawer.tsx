"use client";

import { useQuery } from "@tanstack/react-query";
import { Copy, Database, ExternalLink, Globe, Mail, MapPin, Phone, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatDate, humanize, osmUrl, websiteUrl } from "@/lib/format";
import type { CompanyDetail, CompanyRef, NearbyCompany } from "@/lib/types";
import { MiniMap } from "./map";
import { Badge, ErrorBox, Loading, Meter } from "./ui";

export function CompanyDrawer({ selected, onClose }: { selected: CompanyRef | null; onClose: () => void }) {
  // Clicking a nearby company navigates inside the drawer; reopening resets that.
  const [navigation, setNavigation] = useState<{ from: CompanyRef | null; to: CompanyRef } | null>(null);
  const current = navigation && navigation.from === selected ? navigation.to : selected;
  const setCurrent = (to: CompanyRef) => setNavigation({ from: selected, to });

  useEffect(() => {
    if (!selected) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selected, onClose]);

  const { data, error, isLoading } = useQuery({
    queryKey: ["company", current?.osm_type, current?.osm_id],
    queryFn: () =>
      api<{ company: CompanyDetail; nearby: NearbyCompany[] }>(`/api/companies/${current!.osm_type}/${current!.osm_id}`),
    enabled: !!current,
  });

  if (!selected || !current) return null;

  const company = data?.company;

  return (
    <div className="fixed inset-0 z-[1300] flex justify-end" role="dialog" aria-modal aria-label="Company details">
      <button aria-label="Close details" className="absolute inset-0 bg-black/25" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-lg flex-col overflow-hidden border-l border-line bg-surface shadow-card">
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold leading-snug">{company?.name ?? "Loading…"}</h2>
            {company?.name_local && company.name_local !== company.name && (
              <p className="text-sm text-ink-2" dir="auto">
                {company.name_local}
              </p>
            )}
            {company && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                <Badge>{company.category_group}</Badge>
                <Badge>
                  {company.category_key}={company.category}
                </Badge>
                {company.brand && <Badge>Chain: {company.brand}</Badge>}
              </div>
            )}
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-md p-1 text-ink-2 hover:bg-surface-hover">
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {isLoading && <Loading />}
          {error && <ErrorBox error={error} className="m-5" />}
          {company && (
            <div className="flex flex-col gap-5 px-5 py-4">
              <div className="h-44 overflow-hidden rounded-lg border border-line">
                <MiniMap lat={company.latitude} lon={company.longitude} />
              </div>

              <Section title="Contact">
                <ContactRow icon={Phone} value={company.phone} href={company.phone ? `tel:${company.phone.split(/[;,]/)[0]}` : undefined} />
                <ContactRow icon={Globe} value={company.website} href={company.website ? websiteUrl(company.website) : undefined} external />
                <ContactRow icon={Mail} value={company.email} href={company.email ? `mailto:${company.email}` : undefined} />
                <ContactRow
                  icon={MapPin}
                  value={[company.addr_housenumber, company.addr_street, company.addr_postcode, company.addr_city].filter(Boolean).join(", ") || null}
                />
                {company.opening_hours && <p className="text-sm text-ink-2">Hours: {company.opening_hours}</p>}
                <div className="mt-1 flex items-center gap-3">
                  <span className="text-xs text-muted">Completeness</span>
                  <Meter value={company.completeness} className="max-w-40" />
                  <span className="tabular text-xs text-ink-2">{company.completeness}%</span>
                </div>
              </Section>

              <Section title="Location">
                <Facts
                  facts={[
                    ["City", company.city],
                    ["Locality", company.locality ? `${company.locality} (${company.locality_distance_km} km)` : null],
                    ["District", company.district],
                    ["Province", company.province],
                    ["Coordinates", `${company.latitude.toFixed(6)}, ${company.longitude.toFixed(6)}`],
                  ]}
                />
              </Section>

              <Section title="OpenStreetMap">
                <Facts
                  facts={[
                    ["Element", `${company.osm_type === "n" ? "node" : company.osm_type === "w" ? "way" : "relation"} ${company.osm_id}`],
                    ["Version", company.version],
                    ["Last edited", `${formatDate(company.last_edited)}${company.last_editor ? ` by ${company.last_editor}` : ""}`],
                    ["Operator", company.operator],
                  ]}
                />
                <div className="mt-2 flex flex-wrap gap-2">
                  <a
                    href={osmUrl(company.osm_type, company.osm_id)}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-sm text-accent-ink hover:underline"
                  >
                    Open on openstreetmap.org <ExternalLink size={13} />
                  </a>
                  <Link
                    href={`/query?sql=${encodeURIComponent(
                      `SELECT *\nFROM osm_app.companies\nWHERE osm_type = '${company.osm_type}' AND osm_id = ${company.osm_id};`,
                    )}`}
                    className="inline-flex items-center gap-1 text-sm text-accent-ink hover:underline"
                  >
                    <Database size={13} /> Open in query runner
                  </Link>
                </div>
              </Section>

              <Section title={`All tags (${Object.keys(company.tags).length})`}>
                <div className="overflow-hidden rounded-lg border border-line">
                  <table className="w-full text-sm">
                    <tbody>
                      {Object.entries(company.tags)
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([key, value]) => (
                          <tr key={key} className="border-b border-line/60 last:border-0">
                            <td className="w-2/5 bg-surface-2 px-2.5 py-1 align-top font-mono text-xs text-ink-2">{key}</td>
                            <td className="break-all px-2.5 py-1 text-xs" dir="auto">
                              {value}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </Section>

              {data.nearby.length > 0 && (
                <Section title="Nearby companies">
                  <ul className="flex flex-col">
                    {data.nearby.map((n) => (
                      <li key={`${n.osm_type}${n.osm_id}`}>
                        <button
                          onClick={() => setCurrent({ osm_type: n.osm_type, osm_id: n.osm_id })}
                          className="flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left text-sm hover:bg-surface-hover"
                        >
                          <span className="min-w-0">
                            <span className="block truncate">{n.name}</span>
                            <span className="block text-xs text-muted">{humanize(n.category)}</span>
                          </span>
                          <span className="tabular shrink-0 text-xs text-ink-2">{n.distance_m} m</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      <div className="flex flex-col gap-1.5">{children}</div>
    </section>
  );
}

function Facts({ facts }: { facts: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1 text-sm">
      {facts.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-muted">{label}</dt>
          <dd className="min-w-0 break-words">{value ?? <span className="text-muted">–</span>}</dd>
        </div>
      ))}
    </dl>
  );
}

function ContactRow({
  icon: Icon,
  value,
  href,
  external,
}: {
  icon: typeof Phone;
  value: string | null;
  href?: string;
  external?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <Icon size={14} className="shrink-0 text-muted" />
      {value ? (
        <>
          {href ? (
            <a href={href} target={external ? "_blank" : undefined} rel="noreferrer" className="min-w-0 truncate text-accent-ink hover:underline">
              {value}
            </a>
          ) : (
            <span className="min-w-0 truncate">{value}</span>
          )}
          <button
            aria-label="Copy"
            title="Copy"
            onClick={() => navigator.clipboard.writeText(value)}
            className="rounded p-0.5 text-muted hover:text-ink"
          >
            <Copy size={12} />
          </button>
        </>
      ) : (
        <span className="text-muted">Not available</span>
      )}
    </div>
  );
}
