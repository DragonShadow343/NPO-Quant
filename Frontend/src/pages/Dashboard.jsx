// src/pages/Dashboard.jsx
import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, BarChart, Bar, PieChart, Pie, Cell } from "recharts";

const Dashboard = () => {
  const [data, setData] = useState(null);
  const [carbonSlide, setCarbonSlide] = useState(0);
  const [financeSlide, setFinanceSlide] = useState(0);
  const [dlBusy, setDlBusy] = useState(false);
  const [dlMsg,  setDlMsg]  = useState("");
  const navigate = useNavigate();

  const companyName = "Demo Nonprofit Organization";

  const downloadAll = async () => {
    setDlBusy(true);
    setDlMsg("");
    const reports = [
      ["http://localhost:8000/export/pdf",                  "NPOQuant_Carbon_Report.pdf"],
      ["http://localhost:8000/export/financial-statements", "NPOQuant_Financial_Statements.pdf"],
      ["http://localhost:8000/export/grants",               "NPOQuant_Grant_Readiness.pdf"],
    ];
    const errors = [];
    for (const [url, name] of reports) {
      try {
        const res  = await fetch(url);
        if (!res.ok) throw new Error(name);
        const blob = await res.blob();
        const a    = Object.assign(document.createElement("a"), {
          href: URL.createObjectURL(blob), download: name,
        });
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(a.href);
      } catch { errors.push(name); }
    }
    setDlBusy(false);
    setDlMsg(errors.length ? `Failed: ${errors.join(", ")}` : "All 3 reports downloaded!");
  };
  useEffect(() => {
    fetch("http://localhost:8000/dashboard-data")
      .then(res => res.json())
      .then(json => setData(json))
      .catch(err => {
        console.error("Dashboard fetch failed:", err);
        setData({ carbon: [], accounting: [], grants: [], category_totals: {} });
      });
  }, []);
  

  if (!data) return <div>Loading dashboard...</div>;
  const { carbon = [], accounting = [], grants = [], grants_detail = {}, category_totals = {} } = data;

  const latestCarbon     = carbon.length     ? carbon[carbon.length - 1]           : { total_emissions: 0, composition: {} };
  const prevCarbon       = carbon.length > 1 ? carbon[carbon.length - 2]           : null;
  const latestAccounting = accounting.length ? accounting[accounting.length - 1]   : { inflow: 0, expense: 0, profit_loss: 0 };
  const prevAccounting   = accounting.length > 1 ? accounting[accounting.length - 2] : null;
  const grantCovered     = grants_detail?.covered_count ?? 0;
  const grantTotal       = grants_detail?.total_categories ?? 4;

  const emissionsTrend = prevCarbon
    ? (((latestCarbon.total_emissions - prevCarbon.total_emissions) / prevCarbon.total_emissions) * 100).toFixed(1)
    : 0;

  const profitTrend = prevAccounting
    ? (((latestAccounting.profit_loss - prevAccounting.profit_loss) / Math.abs(prevAccounting.profit_loss)) * 100).toFixed(1)
    : 0;

  const currentEmissions = latestCarbon.total_emissions;
  const currentProfit = latestAccounting.profit_loss;

  const COLORS = ["#0088FE", "#00C49F", "#FFBB28", "#FF8042", "#8884d8"];
  const CARBON_LABELS = { fuel: "Fuel", electricity: "Electricity", natural_gas: "Natural Gas", mileage: "Vehicle Mileage", travel_scope3: "Travel (Scope 3)" };

  return (
    <div className="pb-20 relative">
        <button
          onClick={() => navigate("/")}
          className="absolute top-6 left-6 px-3 py-1.5 rounded-lg bg-white border border-gray-200 text-gray-600 text-sm hover:bg-gray-50 transition-colors cursor-pointer"
        >
          ← Back
        </button>
        <div className="text-center mt-8">
          <h1 className="text-3xl font-bold">{companyName}</h1>
          <p className="text-gray-500">Sustainability & Financial Dashboard</p>

          {/* Preparedness Score */}
          <div className="mt-4 flex justify-center">
            <div className="bg-green-100 text-green-700 px-6 py-3 rounded-2xl font-semibold text-lg border">
              Preparedness Score: 82 / 100
            </div>
          </div>
        </div>

        <div className="max-w-6xl mx-auto mt-10 space-y-8">

          {/* KPI Cards */}
          <div className="grid grid-cols-4 gap-6">
            <div className="bg-white border rounded-2xl p-6 shadow-sm">
              <p className="text-gray-500 text-sm">Current Emissions</p>
              <div className="flex items-center gap-2">
                <h3 className="text-2xl font-semibold">{currentEmissions} kg CO₂e</h3>
                <span className={`text-sm font-semibold ${emissionsTrend > 0 ? "text-red-500" : "text-green-600"}`}>
                  {emissionsTrend > 0 ? "↑" : "↓"} {Math.abs(emissionsTrend)}%
                </span>
              </div>
            </div>

            <div className="bg-white border rounded-2xl p-6 shadow-sm">
              <p className="text-gray-500 text-sm">Monthly Profit</p>
              <div className="flex items-center gap-2">
                <h3 className="text-2xl font-semibold">${currentProfit}</h3>
                <span className={`text-sm font-semibold ${profitTrend > 0 ? "text-green-600" : "text-red-500"}`}>
                  {profitTrend > 0 ? "↑" : "↓"} {Math.abs(profitTrend)}%
                </span>
              </div>
            </div>

            <div className="bg-white border rounded-2xl p-6 shadow-sm">
              <p className="text-gray-500 text-sm">Grant Eligibility</p>
              <h3 className="text-2xl font-semibold">{grantCovered} / {grantTotal}</h3>
              <p className="text-xs text-gray-500 mt-1">categories documented</p>
            </div>
            <div
              onClick={!dlBusy ? downloadAll : undefined}
              className={`${dlBusy ? "bg-gray-300 cursor-not-allowed" : "bg-blue-400 hover:bg-blue-500 cursor-pointer"} duration-150 flex flex-col justify-center items-center rounded-2xl shadow-sm p-6 gap-1`}
            >
              <span className="text-white text-xl text-center font-semibold">
                {dlBusy ? "Generating…" : "Download Reports (PDF)"}
              </span>
              {dlMsg && (
                <span className={`text-xs text-center ${dlMsg.startsWith("Failed") ? "text-red-200" : "text-green-100"}`}>
                  {dlMsg}
                </span>
              )}
            </div>
            {/* <div className="bg-white border rounded-2xl p-6 shadow-sm">
              <a href="#" download className="text-gray-500 text-sm">Download Report as PDF</a>
            </div> */}
            {/* <div className="bg-white border rounded-2xl p-6 shadow-sm">
              <p className="text-gray-500 text-sm">Months of Reserve</p>
              <h3 className="text-2xl font-semibold">{(latestAccounting.inflow / latestAccounting.expense).toFixed(1)}</h3>
            </div> */}
          </div>

          <div className="grid grid-cols-6 gap-8">

            {/* Insights / Paragraph Section */}
            <div className="bg-gray-100 rounded-3xl p-8 border col-span-2 space-y-4">
              <h2 className="text-xl font-semibold">Key Insights</h2>
              <div className="flex flex-col gap-2 mb-4">
                {emissionsTrend > 5 && (
                  <div className="bg-yellow-100 text-yellow-800 px-3 py-2 rounded-lg text-sm font-medium">
                    ⚠ Emissions increased {emissionsTrend}% this month
                  </div>
                )}

                {profitTrend > 0 && (
                  <div className="bg-green-100 text-green-800 px-3 py-2 rounded-lg text-sm font-medium">
                    ✓ Profit improved by {profitTrend}% compared to last month
                  </div>
                )}

                {grantTotal > 0 && grantCovered >= Math.ceil(grantTotal * 0.7) && (
                  <div className="bg-blue-100 text-blue-800 px-3 py-2 rounded-lg text-sm font-medium">
                    ✓ Organization qualifies for most sustainability grants
                  </div>
                )}
              </div>

              <p className="text-gray-600">
                Emissions have increased slightly in March primarily due to higher fuel usage.
                Electricity consumption has remained relatively stable.
              </p>

              {/* <p className="text-gray-600">
                Financial performance improved this quarter with higher inflow and
                increased profitability compared to February.
              </p>

              <p className="text-gray-600">
                Based on the current financial and operational data, the organization
                qualifies for approximately 80% of the evaluated sustainability grants.
              </p> */}
            </div>

            {/* Financial Breakdown (replaces grants chart) */}
            <div className="bg-gray-100 rounded-3xl p-10 border col-span-4">
              <h2 className="text-xl font-semibold mb-4 text-center">Financial Breakdown by Category</h2>
              <BarChart
                width={600}
                height={300}
                data={Object.entries(category_totals).map(([category, amount]) => ({ category, amount }))}
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="category" tick={{ fontSize: 11 }} />
                <YAxis />
                <Tooltip formatter={(v) => [`$${Number(v).toLocaleString()}`, "Amount"]} />
                <Legend />
                <Bar dataKey="amount" fill="#8884d8" name="Amount ($)" />
              </BarChart>
            </div>

            {/* Carbon Carousel */}
            <div className="bg-gray-100 rounded-3xl p-10 border col-span-3">
              <h2 className="text-xl font-semibold mb-4">Carbon Emissions</h2>

              {carbonSlide === 0 && (
                <LineChart width={450} height={300} data={carbon}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="month" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Line type="" dataKey="total_emissions" stroke="#8884d8" name="Total Emissions" />
                </LineChart>
              )}

              {carbonSlide === 1 && (
                <PieChart width={450} height={300}>
                  <Tooltip formatter={(v) => [`${Number(v).toFixed(1)} kg CO₂e`, ""]} />
                  <Pie
                    data={
                      carbon[carbon.length - 1]?.composition
                        ? Object.entries(carbon[carbon.length - 1].composition).map(([key, value]) => ({
                            name: CARBON_LABELS[key] || key,
                            value,
                          }))
                        : []
                    }
                    dataKey="value"
                    nameKey="name"
                    cx="50%"
                    cy="45%"
                    outerRadius={100}
                    label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                  >
                    {carbon[carbon.length - 1]?.composition &&
                      Object.keys(carbon[carbon.length - 1].composition).map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                  </Pie>
                  <Legend />
                </PieChart>
              )}

              <div className="flex justify-between items-center mt-4">
                <button
                  className="px-3 py-1 bg-gray-300 rounded"
                  onClick={() => setCarbonSlide((carbonSlide - 1 + 2) % 2)}
                >
                  ←
                </button>

                <div className="text-sm text-gray-500">
                  {carbonSlide + 1} / 2
                </div>

                <button
                  className="px-3 py-1 bg-gray-300 rounded"
                  onClick={() => setCarbonSlide((carbonSlide + 1) % 2)}
                >
                  →
                </button>
              </div>
            </div>

            {/* Finance Carousel */}
            <div className="bg-gray-100 rounded-3xl p-10 border col-span-3">
              <h2 className="text-xl font-semibold mb-4">Financial Overview</h2>

                <LineChart width={450} height={300} data={accounting}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="month" />
                  <YAxis />
                  <Tooltip />
                  <Legend />
                  <Line type="" dataKey="profit_loss" stroke="#82ca9d" name="Profit/Loss" />
                  <Line type="" dataKey="inflow" stroke="#8884d8" name="Inflow" />
                  <Line type="" dataKey="expense" stroke="#ff7300" name="Expense" />
                </LineChart>
            </div>
          </div>
        </div>
    </div>
  );
};

export default Dashboard;