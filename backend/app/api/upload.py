# app/api/upload.py
from fastapi import APIRouter, UploadFile, File
from typing import List
from app.services import emissions_service, grants_service, report_service
from ocr import extract_text
from ai import extract_data

router = APIRouter()

@router.post("/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    results = []

    for file in files:
        # Read the file into memory
        contents = await file.read()

        # 1. OCR extraction
        text = extract_text(contents)

        # 2. AI parsing
        parsed_data = extract_data(text)

        # 3. Categorize & call services
        file_result = {"filename": file.filename}

        if parsed_data.get("category") == "emissions":
            file_result["emissions"] = emissions_service.estimate(parsed_data)
        elif parsed_data.get("category") == "grant":
            file_result["grant_analysis"] = grants_service.analyze(parsed_data)
        elif parsed_data.get("category") == "report":
            file_result["report"] = report_service.generate(parsed_data)
        else:
            file_result["raw"] = parsed_data

        results.append(file_result)

    return {"files": results}