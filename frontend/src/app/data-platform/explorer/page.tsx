import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { ExplorerPage } from "./explorer";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <ExplorerPage />
    </Suspense>
  );
}
