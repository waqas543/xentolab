import clsx from "clsx";
import { Card } from "./ui";

export function StatTile({
  label,
  value,
  detail,
  className,
}: {
  label: string;
  value: React.ReactNode;
  detail?: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={clsx("px-4 py-3", className)}>
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tracking-tight text-ink">{value}</div>
      {detail && <div className="mt-0.5 text-xs text-ink-2">{detail}</div>}
    </Card>
  );
}
