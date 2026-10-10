import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { AnalyticsPage } from "./analytics";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <AnalyticsPage />
    </Suspense>
  );
}
