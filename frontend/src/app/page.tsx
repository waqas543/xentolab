import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { OverviewPage } from "./overview";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <OverviewPage />
    </Suspense>
  );
}
