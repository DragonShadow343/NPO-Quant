import { useState } from 'react'
import { Routes, Route, useNavigate } from 'react-router-dom'
import Navbar from './components/Navbar.jsx'
import DocumentUploader from './components/DocumentUploader.jsx'
import ReviewPage from './pages/ReviewPage.jsx'
import Dashboard from './pages/Dashboard.jsx'

function UploadFlow() {
  const [view, setView]       = useState("upload")
  const [results, setResults] = useState([])
  const navigate = useNavigate()

  const TAB_MAP = { Documents: "upload", Review: "review", Dashboard: "dashboard" }

  return (
    <div className="h-screen w-full relative flex flex-col">
      {/* <Navbar
        activeTab={view === "upload" ? "Documents" : view === "review" ? "Review" : "Dashboard"}
        onTabChange={(tab) => {
          if (tab === "Dashboard") { navigate("/dashboard"); return; }
          const v = TAB_MAP[tab]
          if (v) setView(v)
        }}
      /> */}

      {view === "upload" && (
        <DocumentUploader
          onDone={(res) => { setResults(res); setView("review") }}
        />
      )}

      {view === "review" && (
        <ReviewPage
          results={results}
          setResults={setResults}
          onBack={() => setView("upload")}
        />
      )}
    </div>
  )
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<UploadFlow />} />
      <Route path="/dashboard" element={<Dashboard />} />
    </Routes>
  )
}

export default App