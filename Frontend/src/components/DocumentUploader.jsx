import { useState } from "react";

const STATUS_STYLE = {
  queued:     "bg-gray-200 text-gray-600",
  processing: "bg-yellow-100 text-yellow-700",
  done:       "bg-green-100 text-green-700",
  review:     "bg-orange-100 text-orange-700",
  error:      "bg-red-100 text-red-700",
};

export default function DocumentUploader({ onDone }) {
  const [files, setFiles]         = useState([]);
  const [error, setError]         = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress]   = useState([]);

  const addFiles = (incoming) => {
    const duplicates = incoming.some((nf) =>
      files.some(
        (ex) => ex.name === nf.name && ex.size === nf.size && ex.lastModified === nf.lastModified
      )
    );
    if (duplicates)                              { setError("Duplicate file detected"); return; }
    if (files.length + incoming.length > 25)     { setError("25 files maximum"); return; }
    setError("");
    setFiles((prev) => [...prev, ...incoming]);
  };

  const handleFileChange = (e) => addFiles(Array.from(e.target.files));
  const handleDrop       = (e) => { e.preventDefault(); addFiles(Array.from(e.dataTransfer.files)); };
  const handleDragOver   = (e) => e.preventDefault();

  const handleSubmit = async () => {
    if (files.length === 0) { setError("No files to upload"); return; }

    setUploading(true);
    setError("");
    setProgress(files.map((f) => ({ name: f.name, status: "queued" })));

    const collected = [];

    for (let i = 0; i < files.length; i++) {
      setProgress((p) => p.map((s, j) => j === i ? { ...s, status: "processing" } : s));

      const formData = new FormData();
      formData.append("file", files[i]);

      try {
        const res = await fetch("http://localhost:8000/upload", {
          method: "POST",
          body: formData,
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        collected.push(data);
        setProgress((p) =>
          p.map((s, j) =>
            j === i ? { ...s, status: data.needs_manual_review ? "review" : "done" } : s
          )
        );
      } catch (err) {
        setProgress((p) => p.map((s, j) => j === i ? { ...s, status: "error" } : s));
        setError(`Failed on: ${files[i].name}`);
      }
    }

    setUploading(false);
    if (collected.length > 0) {
      onDone(collected);
    }
  };

  return (
    <div className="h-screen w-full border-2 relative flex justify-around items-center">

      <div className="flex flex-col items-center">
        <label
          className="w-120 h-64 border-2 text-gray-500 hover:border-gray-800 border-gray-300 bg-white rounded-2xl flex items-center justify-center text-3xl cursor-pointer text-center"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
        >
          Insert Document(s)
          <input
            type="file"
            multiple
            accept=".pdf,.csv,.png,.jpg,.jpeg,.tiff,.xlsx"
            onChange={handleFileChange}
            className="hidden"
            disabled={uploading}
          />
        </label>
        <div className="mt-2.5 text-sm text-gray-500">PDF, JPG, PNG, TIFF, CSV, XLSX</div>
        <div className="text-red-500 mt-1 text-sm text-center max-w-xs">{error}</div>
      </div>

      {files.length > 0 && (
        <div className="flex flex-col items-center justify-center mt-16 h-full gap-2">
          <div className="h-1/2 flex flex-col gap-2 overflow-y-auto w-full px-4">
            {files.map((file, index) => (
              <div
                key={index}
                className="flex items-center justify-between w-96 px-3 py-2 rounded-lg bg-gray-100"
              >
                <span className="text-sm truncate flex-1 mr-2">{file.name}</span>
                {progress[index] && (
                  <span
                    className={`text-xs px-2 py-0.5 rounded-full mr-2 whitespace-nowrap ${
                      STATUS_STYLE[progress[index].status] || ""
                    }`}
                  >
                    {progress[index].status}
                  </span>
                )}
                {!uploading && (
                  <button
                    className="text-red-500 text-sm cursor-pointer shrink-0"
                    onClick={() => setFiles((prev) => prev.filter((_, i) => i !== index))}
                  >
                    Delete
                  </button>
                )}
              </div>
            ))}
          </div>
          <button
            className="mt-3 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 disabled:opacity-50"
            onClick={handleSubmit}
            disabled={uploading}
          >
            {uploading ? "Processing..." : "Submit"}
          </button>
        </div>
      )}
    </div>
  );
}
