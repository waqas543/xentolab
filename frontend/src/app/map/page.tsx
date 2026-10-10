import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { MapPage } from "./map";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <MapPage />
    </Suspense>
  );
}
