import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { DashboardPage } from "./dashboard";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <DashboardPage />
    </Suspense>
  );
}
