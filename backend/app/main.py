# app/main.py
import os
from dotenv import load_dotenv
from fastapi import FastAPI
from app.api import emissions

# Load environment variables first
load_dotenv()

# Create FastAPI app
app = FastAPI(
    title="NPO-Quant API",
    description="Backend API for OCR + emissions calculation",
    version="1.0.0"
)

# Include API routes
app.include_router(emissions.router)