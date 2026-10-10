import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { CompaniesPage } from "./companies";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <CompaniesPage />
    </Suspense>
  );
}
