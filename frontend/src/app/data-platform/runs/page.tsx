import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { RunsPage } from "./runs";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <RunsPage />
    </Suspense>
  );
}
