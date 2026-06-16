import { useState } from "react";
import { Tabs } from "../components/ui";
import IamPage from "./IamPage";
import BrandingPage from "./BrandingPage";

// Admin configuration hub. Groups the deployment-config surfaces that don't each
// merit a top-level sidebar entry (identity reference, front-door branding) under
// one "Settings" item. Each tab renders its existing page (its own header stands
// as the tab heading), so the sidebar stays lean.
const TABS = [
  { id: "iam", label: "Identity & Access" },
  { id: "branding", label: "Branding" },
];

export default function SettingsPage() {
  const [tab, setTab] = useState("iam");
  return (
    <div className="flex flex-col gap-4 pb-8">
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {tab === "iam" && <IamPage />}
      {tab === "branding" && <BrandingPage />}
    </div>
  );
}
