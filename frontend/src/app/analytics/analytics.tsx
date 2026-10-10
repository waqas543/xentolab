"use client";

import { FilterBar } from "@/components/filter-bar";
import { PageHeader, Segmented } from "@/components/ui";
import { useUrlParam } from "@/lib/filters";
import { BrandsTab } from "./brands";
import { PivotTab } from "./pivot";
import { QualityTab } from "./quality";
import { TagsTab } from "./tags";

const TABS = [
  { value: "pivot", label: "Pivot heatmap" },
  { value: "brands", label: "Chains & brands" },
  { value: "quality", label: "Data quality" },
  { value: "tags", label: "Tag explorer" },
] as const;

type Tab = (typeof TABS)[number]["value"];

export function AnalyticsPage() {
  const [tab, setTab] = useUrlParam("tab", "pivot");

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-4 p-4 lg:p-6">
      <PageHeader
        title="Analytics"
        description="Cross-tabulate, compare chains, audit data quality and dig into raw OSM tags."
        actions={<Segmented value={tab as Tab} onChange={setTab} options={TABS.map((t) => ({ ...t }))} />}
      />
      <FilterBar />
      {tab === "pivot" && <PivotTab />}
      {tab === "brands" && <BrandsTab />}
      {tab === "quality" && <QualityTab />}
      {tab === "tags" && <TagsTab />}
    </div>
  );
}
