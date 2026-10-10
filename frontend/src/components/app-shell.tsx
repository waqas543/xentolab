"use client";

import clsx from "clsx";
import { BarChart3, Building2, Database, Gauge, History, LayoutDashboard, Map as MapIcon, Menu, Plug, Table2, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState } from "react";
import { filtersToParams, parseFilters } from "@/lib/filters";
import { ThemeToggle } from "./theme-toggle";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard, filtered: true },
  { href: "/companies", label: "Companies", icon: Building2, filtered: true },
  { href: "/map", label: "Map", icon: MapIcon, filtered: true },
  { href: "/analytics", label: "Analytics", icon: BarChart3, filtered: true },
  { href: "/query", label: "Query runner", icon: Database, filtered: false },
];

const PLATFORM_NAV = [
  { href: "/data-platform", label: "Dashboard", icon: Gauge },
  { href: "/data-platform/sources", label: "Sources", icon: Plug },
  { href: "/data-platform/runs", label: "Pipeline runs", icon: History },
  { href: "/data-platform/explorer", label: "Data explorer", icon: Table2 },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [open, setOpen] = useState(false);

  // Carry the active filters between the filter-scoped pages.
  const filterQuery = filtersToParams(parseFilters(new URLSearchParams(searchParams.toString()))).toString();

  const link = (href: string, label: string, Icon: typeof Gauge, active: boolean) => (
    <Link
      key={href}
      href={href}
      onClick={() => setOpen(false)}
      className={clsx(
        "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
        active ? "bg-accent-soft/60 font-medium text-ink" : "text-ink-2 hover:bg-surface-hover hover:text-ink",
      )}
    >
      <Icon size={16} className={active ? "text-accent" : "text-muted"} />
      {label}
    </Link>
  );

  const nav = (
    <nav className="flex flex-col gap-0.5">
      <div className="px-2.5 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Data platform</div>
      {PLATFORM_NAV.map(({ href, label, icon }) =>
        link(href, label, icon, href === "/data-platform" ? pathname === href : pathname.startsWith(href)))}
      <div className="px-2.5 pb-1 pt-4 text-[11px] font-semibold uppercase tracking-wide text-muted">OSM companies (v1)</div>
      {NAV.map(({ href, label, icon: Icon, filtered }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href) && !pathname.startsWith("/data-platform");
        return (
          <Link
            key={href}
            href={filtered && filterQuery ? `${href}?${filterQuery}` : href}
            onClick={() => setOpen(false)}
            className={clsx(
              "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
              active ? "bg-accent-soft/60 font-medium text-ink" : "text-ink-2 hover:bg-surface-hover hover:text-ink",
            )}
          >
            <Icon size={16} className={active ? "text-accent" : "text-muted"} />
            {label}
          </Link>
        );
      })}
    </nav>
  );

  const brand = (
    <Link href="/" className="flex items-center gap-2 px-2.5">
      <span className="grid size-7 place-items-center rounded-md bg-accent text-white">
        <Building2 size={15} />
      </span>
      <span className="leading-tight">
        <span className="block text-sm font-semibold">OSM Companies</span>
        <span className="block text-[11px] text-muted">ds_companies</span>
      </span>
    </Link>
  );

  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col gap-6 border-r border-line bg-surface px-3 py-4 lg:flex">
        {brand}
        {nav}
        <div className="mt-auto flex items-center justify-between px-2.5">
          <span className="text-xs text-muted">Theme</span>
          <ThemeToggle />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-[1100] flex items-center justify-between border-b border-line bg-surface px-4 py-2.5 lg:hidden">
          {brand}
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <button
              aria-label={open ? "Close menu" : "Open menu"}
              onClick={() => setOpen(!open)}
              className="rounded-md p-1.5 hover:bg-surface-hover"
            >
              {open ? <X size={18} /> : <Menu size={18} />}
            </button>
          </div>
        </header>
        {open && <div className="border-b border-line bg-surface p-3 lg:hidden">{nav}</div>}
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
