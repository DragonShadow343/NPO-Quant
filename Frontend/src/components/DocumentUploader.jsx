import React, { useState } from "react";
import Navbar from "./Navbar";

export default function DocumentUploader() {

  const [files, setFiles] = useState([]);
  const [error, setError] = useState("");

  const handleFileChange = (e) => {
    const selectedFiles = Array.from(e.target.files);

    // Smart duplicate detection (name + size + lastModified)
    const duplicates = selectedFiles.some((newFile) =>
      files.some(
        (existing) =>
          existing.name === newFile.name &&
          existing.size === newFile.size &&
          existing.lastModified === newFile.lastModified
      )
    );

    if (duplicates) {
      setError("Duplicate file detected");
      return;
    }

    // Check max file limit
    if (files.length + selectedFiles.length > 25) {
      setError("25 files maximum");
      return;
    }

    setError("");

    // Add new files to the TOP
    setFiles((prev) => [...prev, ...selectedFiles]);
  };

  const handleDrop = (e) => {
    e.preventDefault();

    const droppedFiles = Array.from(e.dataTransfer.files);

    // Smart duplicate detection (name + size + lastModified)
    const duplicates = droppedFiles.some((newFile) =>
      files.some(
        (existing) =>
          existing.name === newFile.name &&
          existing.size === newFile.size &&
          existing.lastModified === newFile.lastModified
      )
    );

    if (duplicates) {
      setError("Duplicate file detected");
      return;
    }

    // Check max file limit
    if (files.length + droppedFiles.length > 25) {
      setError("25 files maximum");
      return;
    }

    setError("");

    // Add files to the TOP
    setFiles((prev) => [...prev, ...droppedFiles]);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
  };

  const uploadAllFiles = async () => {
    if (files.length === 0) {
      setError("No files to upload");
      return;
    }

    const formData = new FormData();

    files.forEach((file) => {
      formData.append("documents", file);
    });

    try {
      const res = await fetch("http://localhost:5000/upload", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        throw new Error("Upload failed");
      }

      setError("");
      alert("Files uploaded successfully");
    } catch (err) {
      setError("Upload failed. Check backend.");
    }
  };

  return (
    <div className="h-screen w-full border-2 relative flex justify-around items-center">
      {/* Top Tabs */}
      <Navbar/>

      {/* Upload Area */}
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
            onChange={handleFileChange}
            className="hidden"
          />
        </label>

        <div className="mt-2.5 text-sm text-gray-500">
          PDF, JPG, PNG, TIFF, CSV
        </div>
        <div className="text-red-500">
            {error}
        </div>
      </div>

        {/* File display area */}
    {files.length > 0 && (
      <div className="flex flex-col items-center justify-center mt-16 h-full gap-2">
        <div className="h-1/2 flex flex-col gap-2">
            {files.map((file, index) => (
                <div
                key={index}
                className="flex items-center justify-between w-96 px-3 py-2 rounded-lg bg-gray-100"
                >
                <span className="text-sm">{file.name}</span>
                <button
                className="text-red-500 text-sm cursor-pointer"
                onClick={() =>
                    setFiles((prev) => prev.filter((_, i) => i !== index))
                }
                >
                Delete
                </button>
            </div>
            ))}
        </div>
        <button
            className="mt-3 px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600"
            onClick={uploadAllFiles}
        >
            Send Reports
        </button>
      </div>
    )}
    </div>
  );
}