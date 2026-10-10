import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { SourcesPage } from "./sources";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <SourcesPage />
    </Suspense>
  );
}
