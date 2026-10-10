"use client";

import dynamic from "next/dynamic";
import { Loading } from "@/components/ui";

// Tabs and settings live in localStorage, so the runner renders in the browser only.
export const QueryRunnerClient = dynamic(() => import("./query-runner").then((m) => m.QueryRunner), {
  ssr: false,
  loading: () => <Loading />,
});
