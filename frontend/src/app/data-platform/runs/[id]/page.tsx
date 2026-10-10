import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { RunDetailPage } from "./run-detail";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <RunDetailPage />
    </Suspense>
  );
}
