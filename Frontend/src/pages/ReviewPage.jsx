import { useState } from "react";
import { useNavigate } from "react-router-dom";
import FlaggedReview from "../components/FlaggedReview";

const API = "http://localhost:8000";

const STATUS_CHIP = {
  approved:            "bg-green-100 text-green-700",
  rejected:            "bg-red-100 text-red-700",
  pending_approval:    "bg-yellow-100 text-yellow-700",
  needs_manual_review: "bg-orange-100 text-orange-700",
};
const STATUS_LABEL = {
  approved: "Approved", rejected: "Rejected",
  pending_approval: "Pending", needs_manual_review: "Needs Review",
};

const DOC_TYPE_LABEL = {
  electricity:          "Electricity",
  natural_gas:          "Natural Gas",
  fuel:                 "Fuel",
  mileage:              "Mileage",
  grant:                "Grant",
  goods_donated:        "Goods Donated",
  payroll:              "Payroll",
  disbursement_voucher: "Expense Log",
  volunteers:           "Volunteers",
  social:               "Social",
  carbon_summary:       "Carbon Report",
  accounting_summary:   "Accounting Report",
  grant_applications:   "Grant Applications",
};

// Module badges — shown based on r.modules keys (a doc can span multiple modules)
const MODULE_CHIP = {
  carbon:     { label: "Carbon",     cls: "bg-sky-100 text-sky-700" },
  accounting: { label: "Accounting", cls: "bg-emerald-100 text-emerald-700" },
  grants:     { label: "Grants",     cls: "bg-purple-100 text-purple-700" },
};

function moduleChips(r) {
  const mods = r.modules || {};
  const keys = Object.keys(mods).filter((k) => MODULE_CHIP[k]);
  if (keys.length === 0) {
    // Fallback for items without modules data (legacy / manual entry)
    const fallback = r.category === "carbon" ? ["carbon"]
                   : r.category === "social" ? ["accounting", "grants"]
                   : r.category === "accounting" ? ["accounting"] : [];
    return fallback.map((k) => (
      <span key={k} className={`text-xs px-1.5 py-0.5 rounded font-medium ${MODULE_CHIP[k].cls}`}>
        {MODULE_CHIP[k].label}
      </span>
    ));
  }
  return keys.map((k) => (
    <span key={k} className={`text-xs px-1.5 py-0.5 rounded font-medium ${MODULE_CHIP[k].cls}`}>
      {MODULE_CHIP[k].label}
    </span>
  ));
}

function extractedCell(r) {
  if (r.category === "social")
    return `${r.total_volunteers ?? 0} volunteers · ${r.total_hours ?? 0} hrs`;
  if (r.doc_type === "disbursement_voucher") {
    const items = r.raw?.items?.length ?? 0;
    const total = r.raw?.cost_dollars ?? r.raw?.amount ?? 0;
    return `${items} line items · $${total.toLocaleString()}`;
  }
  if (r.doc_type === "payroll") {
    const amt = r.raw?.cost_dollars ?? r.raw?.amount ?? 0;
    return `$${amt.toLocaleString()} · ${r.raw?.period ?? ""}`;
  }
  if (r.doc_type === "carbon_summary") {
    const co2e = r.raw?.amount ?? r.kg_co2e ?? 0;
    const comps = r.raw?.components ?? {};
    const parts = Object.entries(comps).map(([k, v]) => `${k}: ${v} kg`);
    return parts.length > 0
      ? `${co2e} kg CO₂e total · ${parts.join(" · ")}`
      : `${co2e} kg CO₂e · ${r.raw?.period ?? ""}`;
  }
  if (r.doc_type === "accounting_summary") {
    const inflow  = r.raw?.total_inflow ?? r.raw?.amount ?? 0;
    const expense = r.raw?.total_expense ?? r.raw?.cost_dollars ?? 0;
    return `$${inflow.toLocaleString()} inflow · $${expense.toLocaleString()} expense · ${r.raw?.period ?? ""}`;
  }
  if (r.raw)
    return `${r.raw.amount ?? "?"} ${r.raw.unit ?? ""} · ${r.raw.period ?? ""}`;
  return "—";
}

function impactCell(r) {
  if (r.doc_type === "carbon_summary" && r.kg_co2e != null)
    return <span className="font-semibold text-gray-800">{r.kg_co2e} kg CO₂e (total)</span>;
  if (r.category === "carbon" && r.kg_co2e != null)
    return <span className="font-semibold text-gray-800">{r.kg_co2e} kg CO₂e</span>;
  if (r.category === "social" && r.community_value_dollars != null)
    return <span className="font-semibold text-gray-800">${r.community_value_dollars?.toLocaleString()}</span>;
  if (r.doc_type === "accounting_summary") {
    const inflow = r.raw?.total_inflow ?? r.raw?.amount ?? 0;
    return <span className="text-gray-600">${inflow.toLocaleString()} inflow</span>;
  }
  if (r.raw?.cost_dollars > 0)
    return <span className="text-gray-600">${r.raw.cost_dollars.toLocaleString()}</span>;
  return <span className="text-gray-300">—</span>;
}

async function downloadPDF(url, filename) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed: ${filename}`);
  const blob = await res.blob();
  const a    = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(blob), download: filename,
  });
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

export default function ReviewPage({ results, setResults, onBack }) {
  const [busy, setBusy]       = useState(new Set());
  const [genBusy, setGenBusy] = useState(false);
  const [genMsg, setGenMsg]   = useState("");
  const navigate = useNavigate();

  const flagged    = results.filter((r) => r.needs_manual_review);
  const reviewable = results.filter((r) => !r.needs_manual_review);
  const pending    = reviewable.filter((r) => r.status === "pending_approval");
  const approved   = reviewable.filter((r) => r.status === "approved");

  const patch = async (id, action) => {
    if (busy.has(id)) return;
    setBusy((s) => new Set([...s, id]));
    try {
      await fetch(`${API}/${action}/${id}`, { method: "POST" });
      setResults((prev) =>
        prev.map((r) =>
          r.id === id ? { ...r, status: action === "approve" ? "approved" : "rejected" } : r
        )
      );
    } finally {
      setBusy((s) => { const n = new Set(s); n.delete(id); return n; });
    }
  };

  const approveAll = () => pending.forEach((r) => patch(r.id, "approve"));

  const handleResolved = (updated) =>
    setResults((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));

  const generateReports = async () => {
    setGenBusy(true);
    setGenMsg("");
    let errors = [];
    for (const [url, name] of [
      [`${API}/export/pdf`,                  "NPOQuant_Carbon_Report.pdf"],
      [`${API}/export/financial-statements`, "NPOQuant_Financial_Statements.pdf"],
      [`${API}/export/grants`,               "NPOQuant_Grant_Readiness.pdf"],
    ]) {
      try { await downloadPDF(url, name); }
      catch (e) { errors.push(name); }
    }
    setGenBusy(false);
    setGenMsg(errors.length ? `Failed: ${errors.join(", ")}` : "All 3 reports downloaded!");
  };

  return (
    <div className="flex-1 w-full bg-gray-50 overflow-y-auto">
      <div className="max-w-6xl mx-auto pt-32 pb-16 px-6 space-y-5">

        {/* ── Header row ─────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h2 className="text-xl font-semibold text-gray-800">Document Review</h2>
            <p className="text-sm text-gray-400 mt-0.5">
              {approved.length} approved
              {pending.length > 0 && <span> · {pending.length} pending</span>}
              {flagged.length  > 0 && <span> · {flagged.length} need input</span>}
            </p>
          </div>

          <div className="flex gap-2 flex-wrap items-center">
            <button
              onClick={onBack}
              className="px-3 py-1.5 rounded-lg bg-white border border-gray-200 text-gray-600 text-sm hover:bg-gray-50 transition-colors cursor-pointer"
            >
              ← Upload More
            </button>
            {pending.length > 0 && (
              <button
                onClick={approveAll}
                className="px-3 py-1.5 rounded-lg border border-gray-300 text-gray-700 text-sm hover:bg-gray-50 transition-colors cursor-pointer"
              >
                Approve All ({pending.length})
              </button>
            )}
            {(approved.length > 0 || pending.length > 0) && (
              <button
                onClick={() => navigate("/dashboard")}
                className="px-4 py-1.5 rounded-lg bg-blue-500 text-white text-sm hover:bg-blue-600 transition-colors cursor-pointer"
              >
                Go to Dashboard →
              </button>
            )}
          </div>
        </div>

        {genMsg && (
          <p className={`text-sm ${genMsg.startsWith("Failed") ? "text-red-500" : "text-green-600"}`}>
            {genMsg}
          </p>
        )}

        {/* ── Flagged / manual-review items ─────────────────────────────── */}
        {flagged.length > 0 && (
          <div className="rounded-xl border border-orange-200 bg-white overflow-hidden">
            <div className="px-4 py-3 bg-orange-50 border-b border-orange-100">
              <h3 className="text-sm font-semibold text-orange-700">
                Needs Input
                <span className="ml-2 font-normal text-orange-400">({flagged.length})</span>
              </h3>
              <p className="text-xs text-orange-500 mt-0.5">
                Only images require manual classification — other formats are auto-processed.
              </p>
            </div>
            <div className="p-4">
              <FlaggedReview flagged={flagged} onResolved={handleResolved} />
            </div>
          </div>
        )}

        {/* ── AI-Detected Documents table ────────────────────────────────── */}
        {reviewable.length > 0 && (
          <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-gray-700">
                AI-Detected Documents
                <span className="ml-2 text-gray-400 font-normal">({reviewable.length})</span>
              </h3>
              <div className="flex gap-2 text-xs flex-wrap">
                {["carbon","accounting","grants"].map((mod) => {
                  const count = reviewable.filter(r => r.modules?.[mod] || (mod === "accounting" && r.category === "accounting") || (mod === "carbon" && r.category === "carbon")).length;
                  if (!count) return null;
                  const chip = MODULE_CHIP[mod];
                  return (
                    <span key={mod} className={`px-2 py-0.5 rounded-full font-medium ${chip.cls}`}>
                      {count} {chip.label.toLowerCase()}
                    </span>
                  );
                })}
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50/50 text-xs text-gray-400 uppercase tracking-wide">
                    <th className="px-4 py-2.5 text-left font-medium">File</th>
                    <th className="px-4 py-2.5 text-left font-medium">Type</th>
                    <th className="px-4 py-2.5 text-left font-medium">Extracted</th>
                    <th className="px-4 py-2.5 text-left font-medium">Impact</th>
                    <th className="px-4 py-2.5 text-left font-medium">Status</th>
                    <th className="px-4 py-2.5 text-left font-medium">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {reviewable.map((r) => (
                    <tr
                      key={r.id}
                      className={`hover:bg-gray-50/60 transition-colors
                        ${r.status === "approved" ? "bg-green-50/30" : ""}
                        ${r.status === "rejected" ? "opacity-40" : ""}`}
                    >
                      {/* File */}
                      <td className="px-4 py-3">
                        <p className="text-gray-700 font-medium truncate max-w-[160px]" title={r.filename}>
                          {r.filename}
                        </p>
                        {r.confidence && (
                          <p className="text-xs text-gray-300 mt-0.5 capitalize">{r.confidence}</p>
                        )}
                      </td>

                      {/* Type */}
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-1">
                          <p className="text-xs font-medium text-gray-600">
                            {DOC_TYPE_LABEL[r.doc_type] ?? (r.doc_type || "Unknown").replace(/_/g, " ")}
                          </p>
                          <div className="flex flex-wrap gap-1">
                            {moduleChips(r)}
                          </div>
                          {r.scope != null && (
                            <span className="text-xs text-gray-400">Scope {r.scope}</span>
                          )}
                        </div>
                      </td>

                      {/* Extracted */}
                      <td className="px-4 py-3 text-gray-500 text-xs max-w-[180px]">
                        {extractedCell(r)}
                      </td>

                      {/* Impact */}
                      <td className="px-4 py-3 text-sm">
                        {impactCell(r)}
                      </td>

                      {/* Status */}
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_CHIP[r.status] ?? "bg-gray-100 text-gray-600"}`}>
                          {STATUS_LABEL[r.status] ?? r.status}
                        </span>
                      </td>

                      {/* Action */}
                      <td className="px-4 py-3">
                        {r.status === "pending_approval" ? (
                          <div className="flex gap-1.5">
                            <button
                              disabled={busy.has(r.id)}
                              onClick={() => patch(r.id, "approve")}
                              className="px-2.5 py-1 bg-blue-500 text-white text-xs rounded-lg hover:bg-blue-600 disabled:opacity-40 transition-colors cursor-pointer"
                            >
                              {busy.has(r.id) ? "…" : "Approve"}
                            </button>
                            <button
                              disabled={busy.has(r.id)}
                              onClick={() => patch(r.id, "reject")}
                              className="px-2.5 py-1 bg-white border border-gray-200 text-gray-500 text-xs rounded-lg hover:bg-gray-50 disabled:opacity-40 transition-colors cursor-pointer"
                            >
                              Reject
                            </button>
                          </div>
                        ) : r.status === "approved" ? (
                          <span className="text-green-600 text-xs font-medium">✓</span>
                        ) : (
                          <span className="text-gray-300 text-xs">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {results.length === 0 && (
          <div className="flex flex-col items-center py-24 gap-4 text-center">
            <p className="text-5xl text-gray-200">📄</p>
            <p className="text-gray-400">No documents uploaded yet.</p>
            <button
              onClick={onBack}
              className="px-4 py-2 bg-blue-500 text-white rounded-lg text-sm hover:bg-blue-600 cursor-pointer"
            >
              Go to Upload
            </button>
          </div>
        )}

        <p className="text-xs text-gray-300 text-center">
          All AI findings require human approval before inclusion in reports (Bill C-59 compliant)
        </p>
      </div>
    </div>
  );
}
