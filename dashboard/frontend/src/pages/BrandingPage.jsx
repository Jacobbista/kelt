import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Panel, inputCls, btn } from "../components/ui";
import { IconArrowLeft } from "../components/icons";
import { useToast } from "../context/ToastContext";
import { getBranding, setBranding } from "../api";

// Admin page to co-brand the front-door welcome page. KELT stays the primary
// mark; this sets the org/team/centre that OWNS this instance, shown under KELT
// as "operated by <org>" so the welcome page reads as "<org>'s private 5G
// testbed". Writes the frontdoor-brand ConfigMap and rolls the front-door.
const MAX_LOGO_BYTES = 480 * 1024; // stay under the backend 512KB / ConfigMap cap

// Sample a logo's average luminance (opaque pixels only) to suggest a backdrop:
// a dark logo on the dark welcome page needs a light plate. Resolves to "light"
// or "" (none). Best-effort: any failure (taint, decode) yields no backdrop.
function detectLogoBackdrop(dataUri) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      try {
        const w = Math.min(img.naturalWidth || 64, 64) || 64;
        const h = Math.min(img.naturalHeight || 64, 64) || 64;
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        const ctx = c.getContext("2d");
        ctx.drawImage(img, 0, 0, w, h);
        const d = ctx.getImageData(0, 0, w, h).data;
        let lum = 0, n = 0;
        for (let i = 0; i < d.length; i += 4) {
          if (d[i + 3] < 32) continue; // skip transparent
          lum += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
          n++;
        }
        resolve(n && lum / n < 110 ? "light" : "");
      } catch { resolve(""); }
    };
    img.onerror = () => resolve("");
    img.src = dataUri;
  });
}

export default function BrandingPage() {
  const toast = useToast();
  const [orgName, setOrgName] = useState("");
  const [tagline, setTagline] = useState("");
  const [accent, setAccent] = useState("#14b8a6");
  const [logo, setLogo] = useState(""); // data-URI
  const [logoBg, setLogoBg] = useState(""); // "" | "light" | "dark"
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    getBranding().then((b) => {
      setOrgName(b.org_name || "");
      setTagline(b.tagline || "");
      setAccent(b.accent || "#14b8a6");
      setLogo(b.org_logo || "");
      setLogoBg(b.logo_bg || "");
    }).catch(() => {}).finally(() => setLoaded(true));
  }, []);

  const onFile = (f) => {
    if (!f) return;
    if (f.size > MAX_LOGO_BYTES) { toast.error("Image too large (max ~480 KB). Use a smaller PNG/SVG."); return; }
    const reader = new FileReader();
    reader.onload = () => {
      const uri = String(reader.result || "");
      setLogo(uri);
      // Auto-pick a backdrop for contrast; the operator can override below.
      detectLogoBackdrop(uri).then((bg) => { setLogoBg(bg); if (bg === "light") toast.success("Dark logo detected — added a light backdrop for contrast"); });
    };
    reader.onerror = () => toast.error("Could not read the image");
    reader.readAsDataURL(f);
  };

  const save = async () => {
    setBusy(true);
    try {
      await setBranding({ org_name: orgName.trim(), tagline: tagline.trim(), accent: accent.trim(), org_logo: logo, logo_bg: logoBg });
      toast.success("Branding applied — front-door rolling out");
    } catch (e) { toast.error(`Save failed: ${e.message}`); }
    finally { setBusy(false); }
  };

  const clearAll = () => { setOrgName(""); setTagline(""); setAccent("#14b8a6"); setLogo(""); setLogoBg(""); };

  // Backdrop style applied behind the logo (preview + welcome page) for contrast.
  const plateStyle = logoBg === "light"
    ? { background: "#ffffff", padding: "6px 10px", borderRadius: 8, display: "inline-flex" }
    : logoBg === "dark"
      ? { background: "#0b1120", border: "1px solid #1e293b", padding: "6px 10px", borderRadius: 8, display: "inline-flex" }
      : logoBg === "glass"
        ? { background: "rgba(148,163,184,.10)", border: "1px solid rgba(148,163,184,.22)", borderRadius: 10, padding: "6px 10px", display: "inline-flex", backdropFilter: "blur(6px)", boxShadow: "0 2px 14px rgba(0,0,0,.28)" }
        : { display: "inline-flex" };

  return (
    <div className="svc-fade flex flex-col gap-4 pb-8">
        <Link to="/settings" className="inline-flex w-fit items-center gap-1 text-xs text-slate-400 hover:text-slate-200">
          <IconArrowLeft size={14} /> Settings
        </Link>
      <header>
        <h2 className="text-lg font-semibold">Branding</h2>
        <p className="text-xs text-slate-400">
          Co-brand the front-door welcome page. KELT stays the primary mark; the org you set
          appears under it as the operator, so the page reads as your private 5G testbed.
        </p>
      </header>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Organization">
          <div className="flex flex-col gap-3 text-xs">
            <label className="flex flex-col gap-1">
              <span className="text-slate-400">Organization name</span>
              <input className={inputCls} placeholder="e.g. RISE Research Institutes" value={orgName} onChange={(e) => setOrgName(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-slate-400">Tagline (optional, overrides "Private 5G testbed")</span>
              <input className={inputCls} placeholder="Private 5G testbed" value={tagline} onChange={(e) => setTagline(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-slate-400">Accent color</span>
              <div className="flex items-center gap-2">
                <input type="color" className="h-8 w-12 rounded border border-slate-700 bg-slate-900" value={accent} onChange={(e) => setAccent(e.target.value)} />
                <input className={`${inputCls} w-28`} value={accent} onChange={(e) => setAccent(e.target.value)} />
              </div>
            </label>
            <div className="flex flex-col gap-1">
              <span className="text-slate-400">Logo (PNG/SVG, max ~480 KB)</span>
              <div className="flex items-center gap-2">
                <label className="inline-flex w-fit cursor-pointer items-center gap-1 rounded bg-slate-700/60 px-2 py-1 text-slate-300 hover:bg-slate-700">
                  ↑ Upload logo
                  <input type="file" accept="image/png,image/svg+xml,image/jpeg,image/webp" className="hidden" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ""; }} />
                </label>
                {logo && <button type="button" onClick={() => setLogo("")} className={btn.ghost}>remove logo</button>}
              </div>
            </div>
            {logo && (
              <label className="flex flex-col gap-1">
                <span className="text-slate-400">Logo backdrop (contrast)</span>
                <select className={inputCls} value={logoBg} onChange={(e) => setLogoBg(e.target.value)}>
                  <option value="">None (transparent)</option>
                  <option value="light">Light plate</option>
                  <option value="dark">Dark plate</option>
                  <option value="glass">Glass (frosted)</option>
                </select>
                <span className="text-[10px] text-slate-500">Auto-set on upload (light plate for a dark logo); override here.</span>
              </label>
            )}
          </div>
        </Panel>

        <Panel title="Preview">
          <div className="rounded-lg border border-slate-800 p-5 text-center" style={{ background: "radial-gradient(600px 300px at 50% -10%, #0d2b2a 0%, #0b1120 60%)" }}>
            <div className="flex flex-wrap items-center justify-center gap-4">
              <div className="flex items-center gap-2">
                <svg viewBox="0 0 64 64" className="h-9 w-9" aria-hidden="true">
                  <g fill="none" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M16 58 Q32 63 48 58" stroke="#0f766e" strokeWidth="3" />
                    <path d="M21 58 C15 50 27 43 21 35 C15 28 25 21 21 14" stroke="#14b8a6" strokeWidth="4" />
                    <path d="M32 58 C26 49 38 41 32 33 C26 25 38 17 32 8" stroke="#0d9488" strokeWidth="4" />
                    <path d="M43 58 C49 50 37 43 43 35 C49 28 39 22 43 16" stroke="#14b8a6" strokeWidth="4" />
                  </g>
                  <g fill="#f59e0b"><circle cx="21" cy="14" r="2.6" /><circle cx="32" cy="8" r="2.8" /><circle cx="43" cy="16" r="2.6" /></g>
                </svg>
                <span className="text-xl font-semibold tracking-wide text-slate-100">KELT</span>
              </div>
              {(orgName || logo) && (
                <>
                  <span className="h-10 w-px bg-slate-600" />
                  <div className="flex flex-col items-center gap-1.5">
                    <span className="text-[9px] uppercase tracking-[0.16em] text-slate-500">operated by</span>
                    <div className="flex items-center gap-2.5">
                      {logo && <span style={plateStyle}><img src={logo} alt="" style={{ height: 30, maxWidth: 160, objectFit: "contain", display: "block" }} /></span>}
                      {orgName && <span className="text-lg font-semibold" style={{ color: "#f1f5f9" }}>{orgName}</span>}
                    </div>
                  </div>
                </>
              )}
            </div>
            <div className="mt-3 text-sm text-slate-400">{tagline || "Private 5G testbed"}</div>
            <div className="mt-4 inline-block rounded px-2 py-0.5 text-[10px]" style={{ background: `${accent}26`, color: accent }}>accent</div>
          </div>
          <p className="mt-2 text-[10px] text-slate-500">Shown on the front-door welcome page (the public service directory). Saving rolls the front-door to pick it up.</p>
        </Panel>
      </div>

      <div className="flex items-center gap-3">
        <button type="button" onClick={save} disabled={busy || !loaded} className={btn.sky}>{busy ? "Applying…" : "Apply branding"}</button>
        <button type="button" onClick={clearAll} disabled={busy} className={btn.ghost}>Reset to KELT default</button>
      </div>
    </div>
  );
}
