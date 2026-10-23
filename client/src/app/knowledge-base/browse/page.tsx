import Browser from "./Browser";
import type { Manual } from "@/lib/documents/types";

export default async function BrowsePage({
  searchParams,
}: {
  searchParams: Promise<{ manual?: string; node?: string }>;
}) {
  const { manual, node } = await searchParams;
  const manualId: Manual = manual === "HDM" || manual === "CADD" ? manual : "PPM";
  return <Browser key={`${manualId}-${node ?? ""}`} initialManual={manualId} initialNode={node} />;
}
