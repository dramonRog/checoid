import os
import cv2
import logging
from datetime import datetime
from typing import Dict, Any

from ultralytics import YOLO
from paddleocr import PaddleOCR
import pymupdf

from pathlib import Path
import numpy as np

# Import our custom modules
from .detector import detect_receipt
from .vision import process_crop, enforce_portrait_orientation, preprocess_for_ocr
from .ocr import execute_ocr
from .parser import parse_with_llm

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
logging.getLogger().setLevel(logging.ERROR)

# Initialize engines at module level so they stay loaded in memory for the FastAPI server
PROJECT_ROOT = Path(__file__).resolve().parent.parent
YOLO_MODEL_PATH = str(PROJECT_ROOT / "models" / "YOLOv26_OBB_Nano_Receipt_Detection.pt")

try:
    model_yolo = YOLO(YOLO_MODEL_PATH)
except Exception as e:
    print(f"[WARNING] Could not load YOLO model: {e}")
    model_yolo = None

reader_ocr = PaddleOCR(lang="pl", use_angle_cls=True, show_log=False, use_gpu=True)


def process_receipt_end_to_end(image_path: str, debug: bool = False) -> Dict[str, Any]:
    if not os.path.exists(image_path):
        return {"error": f"Image not found at {image_path}"}

    if debug: print(f"[{datetime.now().strftime('%H:%M:%S')}] [1/5] Ingesting raw image...")
    image_cv = cv2.imread(image_path)

    if image_cv is None:
        return {"error": f"Failed to decode image at {image_path}. File may be corrupted."}

    if debug: print(f"[{datetime.now().strftime('%H:%M:%S')}] [2/5] Geometric Isolation (YOLOv8)...")
    if model_yolo is None:
        return {"error": "YOLO model is not initialized."}

    points, _ = detect_receipt(image_cv, model_yolo)
    if points is None:
        return {"error": "YOLO failed to detect a receipt in the image."}

    if debug: print(f"[{datetime.now().strftime('%H:%M:%S')}] [3/5] Optical Preprocessing (OpenCV)...")
    cropped = process_crop(image_cv, points)
    oriented = enforce_portrait_orientation(cropped)
    processed = preprocess_for_ocr(oriented)

    if debug: print(f"[{datetime.now().strftime('%H:%M:%S')}] [4/5] Digitization Matrix (PaddleOCR)...")
    ocr_text = execute_ocr(reader_ocr, processed)

    clean_ocr_text = (ocr_text or "").strip()

    if debug:
        print(f"\n{'-' * 50}\n>> STEP 4: EXTRACTED OCR TEXT\n{'-' * 50}")
        print(clean_ocr_text if clean_ocr_text else "[FATAL ERROR: NO TEXT DETECTED]")
        print("-" * 50)

    if not clean_ocr_text:
        return {"error": "OCR extracted no text."}

    if debug: print(f"[{datetime.now().strftime('%H:%M:%S')}] [5/5] Semantic Parsing (Qwen2.5:7b)...")
    final_payload = parse_with_llm(clean_ocr_text)

    if debug: print(f"[{datetime.now().strftime('%H:%M:%S')}] PIPELINE COMPLETED SUCCESSFULLY.")
    return final_payload


def process_pdf_receipt(pdf_path: str, debug: bool = False) -> Dict[str, Any]:
    """
    Hybrid PDF pipeline:
    1. Tries Direct Text Extraction (Fast Lane for Invoices)
    2. Falls back to direct in-memory OCR (No YOLO/Crop) for flattened e-receipts (Biedronka)
    """
    if not os.path.exists(pdf_path):
        return {"error": f"PDF file not found at {pdf_path}"}

    try:
        if debug:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Inspecting PDF layer data...")

        doc = pymupdf.open(pdf_path)
        extracted_text = ""

        for page in doc:
            text = page.get_text()
            if text:
                extracted_text += text + "\n"

        clean_text = extracted_text.strip()

        # --- FAST LANE: Digital Text Found ---
        if clean_text and len(clean_text) > 50:
            if debug:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Digital text found! Using Fast-Lane LLM...")

            result = parse_with_llm(clean_text)
            doc.close()
            return result

        # --- OCR FALLBACK (For Biedronka / Scanned Receipts) ---
        if debug:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] No text found. Running in-memory PaddleOCR...")

        # Render PDF to pixels at 200 DPI
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=200, colorspace=pymupdf.csRGB, alpha=False)

        # Convert PyMuPDF pixmap directly to OpenCV matrix in RAM!
        img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, 3)
        img_cv = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

        doc.close()

        # Send flat digital image directly to PaddleOCR (Bypass YOLO & Cropping)
        ocr_text = execute_ocr(reader_ocr, img_cv)
        clean_ocr_text = (ocr_text or "").strip()

        if debug:
            print(f"\n{'-' * 50}\n>> EXTRACTED OCR TEXT FROM PDF\n{'-' * 50}")
            print(clean_ocr_text if clean_ocr_text else "[FATAL ERROR: NO TEXT DETECTED]")
            print("-" * 50)

        if not clean_ocr_text:
            return {"error": "OCR extracted no text from the PDF render."}

        if debug:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Semantic Parsing (Qwen2.5:7b)...")

        return parse_with_llm(clean_ocr_text)

    except Exception as e:
        return {"error": f"Failed to process PDF file: {str(e)}"}