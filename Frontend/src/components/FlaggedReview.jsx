import { useState, useEffect } from "react";

const API = "http://localhost:8000";

// ONLY these file types get the manual-input form
const HUMAN_INPUT_EXTS = new Set([".jpg", ".jpeg", ".png"]);

function getExt(filename = "") {
  const m = filename.match(/(\.[^.]+)$/);
  return m ? m[1].toLowerCase() : "";
}

// ── Schemas ──────────────────────────────────────────────────────────────────
const SCHEMAS = {
  electricity:   { amount: 0, unit: "kWh", period: "", provider: "", cost_dollars: 0 },
  natural_gas:   { amount: 0, unit: "GJ",  period: "", provider: "", cost_dollars: 0 },
  fuel:          { amount: 0, unit: "L",   period: "", provider: "", cost_dollars: 0 },
  mileage:       { amount: 0, unit: "km",  period: "", cost_dollars: 0 },
  grant:         { amount: 0, unit: "CAD", period: "", provider: "", cost_dollars: 0 },
  goods_donated: { amount: 0, unit: "CAD", period: "", cost_dollars: 0 },
  social:        { total_hours: 0, volunteers: 1, period: "" },
};

const TYPE_LABELS = {
  electricity:   "Electricity (kWh)",
  natural_gas:   "Natural Gas (GJ)",
  fuel:          "Fuel / Fleet (L)",
  mileage:       "Mileage (km)",
  grant:         "Grant Revenue",
  goods_donated: "Goods Donated",
  social:        "Volunteer Hours",
};

const FIELD_LABELS = {
  amount: "Amount", unit: "Unit", period: "Period (e.g. March 2026)",
  provider: "Provider", cost_dollars: "Billed ($)",
  total_hours: "Total Hours", volunteers: "# Volunteers",
};

function detectType(raw) {
  if (!raw) return "electricity";
  if (raw.volunteers || raw.doc_type === "social") return "social";
  if (raw.type && SCHEMAS[raw.type]) return raw.type;
  return "electricity";
}

function initData(schemaKey, raw) {
  const base = { ...SCHEMAS[schemaKey] };
  if (!raw) return base;
  if (schemaKey === "social") {
    if (raw.total_hours) base.total_hours = raw.total_hours;
    if (raw.volunteers)  base.volunteers  = Array.isArray(raw.volunteers) ? raw.volunteers.length : raw.volunteers;
    if (raw.period && !["Unknown Period", "Unknown"].includes(raw.period)) base.period = raw.period;
  } else {
    if (raw.amount > 0)                              base.amount       = raw.amount;
    if (raw.unit)                                    base.unit         = raw.unit;
    if (raw.period && !["Unknown Period", "Unknown"].includes(raw.period)) base.period = raw.period;
    if (raw.provider && raw.provider !== "Unknown Provider") base.provider = raw.provider;
    if (raw.cost_dollars != null && raw.cost_dollars > 0)  base.cost_dollars = raw.cost_dollars;
  }
  return base;
}

// ── Image preview panel ───────────────────────────────────────────────────────
function ImagePreview({ item, onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-end pointer-events-none">
      <div className="flex-1 pointer-events-auto" onClick={onClose} style={{ background: "rgba(0,0,0,0.25)" }} />
      <div className="pointer-events-auto flex flex-col bg-white border-l border-gray-200 shadow-2xl" style={{ width: "50vw", height: "100vh" }}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 flex-shrink-0">
          <div>
            <p className="text-xs font-semibold text-gray-700">Source Image</p>
            <p className="text-xs text-gray-400 truncate max-w-xs mt-0.5">{item.filename}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-xl leading-none cursor-pointer px-2">✕</button>
        </div>
        <div className="flex-1 overflow-auto p-4 bg-gray-50 flex items-start justify-center">
          <img
            src={`${API}/preview/${item.id}`}
            alt={item.filename}
            className="max-w-full object-contain rounded shadow border border-gray-200"
          />
        </div>
        <div className="px-4 py-2 border-t border-gray-100 flex-shrink-0">
          <p className="text-xs text-gray-400">Review the image, then fill in the fields on the left.</p>
        </div>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function FlaggedReview({ flagged, onResolved }) {
  const [state, setState] = useState(() => {
    const m = {};
    flagged.forEach((f) => {
      const ext  = getExt(f.filename);
      const needsForm = HUMAN_INPUT_EXTS.has(ext);
      if (needsForm) {
        const type = detectType(f.raw_extracted);
        m[f.id] = { type, data: initData(type, f.raw_extracted), open: false, busy: false, error: null };
      }
    });
    return m;
  });
  const [imagePreviewId, setImagePreviewId] = useState(null);

  useEffect(() => {
    const h = (e) => { if (e.key === "Escape") setImagePreviewId(null); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  if (flagged.length === 0) return null;

  const imagePreviewItem = flagged.find((f) => f.id === imagePreviewId) ?? null;
  const update   = (id, patch) => setState((s) => ({ ...s, [id]: { ...s[id], ...patch } }));
  const setField = (id, key, val) => setState((s) => ({ ...s, [id]: { ...s[id], data: { ...s[id].data, [key]: val } } }));

  const changeType = (id, newType) => {
    const raw = flagged.find((f) => f.id === id)?.raw_extracted;
    update(id, { type: newType, data: initData(newType, raw), error: null });
  };

  const submit = async (item) => {
    const { type, data } = state[item.id];
    const period = data.period?.trim();
    if (!period) { update(item.id, { error: "Period is required (e.g. March 2026)" }); return; }
    const amount = parseFloat(type === "social" ? data.total_hours : data.amount) || 0;
    if (!amount)  { update(item.id, { error: "Amount must be greater than 0" }); return; }

    update(item.id, { busy: true, error: null });
    const body = type === "social"
      ? { doc_type: "social", amount: parseFloat(data.total_hours) || 0, unit: "hrs", period,
          provider: "", volunteers: parseInt(data.volunteers) || 1, total_hours: parseFloat(data.total_hours) || 0 }
      : { doc_type: type, amount, unit: data.unit || "", period,
          provider: data.provider || "", cost_dollars: parseFloat(data.cost_dollars) || null };
    try {
      const res = await fetch(`${API}/manual-entry/${item.id}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      const updated = await res.json();
      await fetch(`${API}/approve/${item.id}`, { method: "POST" });
      onResolved({ ...updated, status: "approved" });
      if (imagePreviewId === item.id) setImagePreviewId(null);
    } catch (e) {
      update(item.id, { error: e.message });
    } finally {
      update(item.id, { busy: false });
    }
  };

  // Partition: images (need form) vs non-images (error card)
  const imageItems    = flagged.filter((f) => HUMAN_INPUT_EXTS.has(getExt(f.filename)));
  const nonImageItems = flagged.filter((f) => !HUMAN_INPUT_EXTS.has(getExt(f.filename)));

  return (
    <>
      {imagePreviewItem && (
        <ImagePreview item={imagePreviewItem} onClose={() => setImagePreviewId(null)} />
      )}

      <div className="space-y-2">

        {/* ── Non-image files: scan error cards ───────────────────────── */}
        {nonImageItems.map((item) => {
          const ext = getExt(item.filename);
          return (
            <div key={item.id} className="flex items-center gap-3 rounded-lg border border-red-100 bg-red-50/60 px-4 py-3">
              <span className="text-red-400 text-base shrink-0">⚠</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-700 truncate">{item.filename}</p>
                <p className="text-xs text-red-400 mt-0.5">
                  File could not be scanned — {ext.toUpperCase()} documents are not supported for image OCR.
                </p>
              </div>
              <span className="text-xs text-gray-400 bg-white border border-gray-200 px-2 py-0.5 rounded-full shrink-0">
                {ext.toUpperCase()}
              </span>
            </div>
          );
        })}

        {/* ── Image files: manual input forms ─────────────────────────── */}
        {imageItems.map((item) => {
          const s      = state[item.id];
          if (!s) return null;
          const schema = SCHEMAS[s.type] ?? SCHEMAS.electricity;
          const fields = Object.keys(schema);

          return (
            <div key={item.id} className="rounded-xl border border-orange-200 bg-white shadow-sm overflow-hidden">
              {/* Card header */}
              <div
                className="flex items-center justify-between px-4 py-3 bg-orange-50/60 border-b border-orange-100 cursor-pointer select-none"
                onClick={() => update(item.id, { open: !s.open })}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-orange-400 text-xs">{s.open ? "▾" : "▸"}</span>
                  <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-blue-100 text-blue-700 shrink-0">
                    IMAGE
                  </span>
                  <span className="text-sm text-gray-700 truncate">{item.filename}</span>
                </div>
                <div className="flex items-center gap-2 shrink-0 ml-2" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => setImagePreviewId(imagePreviewId === item.id ? null : item.id)}
                    className={`text-xs px-3 py-1 rounded-full border transition-colors cursor-pointer ${
                      imagePreviewId === item.id
                        ? "border-blue-500 bg-blue-500 text-white"
                        : "border-blue-200 text-blue-600 hover:bg-blue-50"
                    }`}
                  >
                    {imagePreviewId === item.id ? "Close" : "View"}
                  </button>
                  <select
                    value={s.type}
                    onChange={(e) => changeType(item.id, e.target.value)}
                    className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white text-gray-700 focus:outline-none focus:border-blue-400 cursor-pointer"
                  >
                    {Object.entries(TYPE_LABELS).map(([val, label]) => (
                      <option key={val} value={val}>{label}</option>
                    ))}
                  </select>
                </div>
              </div>

              {s.open && (
                <div className="px-4 py-4 space-y-4">
                  <div className="grid grid-cols-2 gap-3">
                    {fields.map((key) => {
                      const isNum = typeof schema[key] === "number";
                      return (
                        <div key={key} className="flex flex-col gap-1">
                          <label className="text-xs font-medium text-gray-400">
                            {FIELD_LABELS[key] || key}
                          </label>
                          <input
                            type={isNum ? "number" : "text"}
                            min={isNum ? "0" : undefined}
                            step={isNum ? "any" : undefined}
                            value={s.data[key] ?? ""}
                            onChange={(e) => setField(item.id, key, isNum ? parseFloat(e.target.value) || 0 : e.target.value)}
                            className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm text-gray-800
                                       focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-50 bg-gray-50"
                            placeholder={String(schema[key] || "")}
                          />
                        </div>
                      );
                    })}
                  </div>

                  <div className="flex items-center gap-3 pt-1">
                    {s.error && <p className="text-xs text-red-500 flex-1">{s.error}</p>}
                    <button
                      onClick={() => submit(item)}
                      disabled={s.busy}
                      className="ml-auto px-4 py-1.5 rounded-lg bg-orange-500 text-white text-xs font-medium
                                 hover:bg-orange-600 disabled:opacity-50 transition-colors cursor-pointer shrink-0"
                    >
                      {s.busy ? "Saving…" : "Save & Approve"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}