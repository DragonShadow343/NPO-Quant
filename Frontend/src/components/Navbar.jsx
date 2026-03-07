import React, {useState} from 'react'

const Navbar = () => {
  const [activeTab, setActiveTab] = useState("Documents");

  return (
    <div className="absolute bg-gray-200 rounded-full gap-x-10 top-10 left-1/2 -translate-x-1/2 flex">
        {["Documents", "Review", "Report"].map((tab) => (
            <button
                key={tab}
                className={`px-4 py-6 rounded-full cursor-pointer text-xl ${activeTab === tab ? "bg-gray-300" : ""}`}
                onClick={() => setActiveTab(tab)}
            >
                {tab}
            </button>
        ))}
    </div>
  )
}

export default Navbar