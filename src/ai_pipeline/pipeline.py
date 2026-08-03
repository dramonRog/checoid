import os
import cv2
import logging
from datetime import datetime
from typing import Dict, Any

from ultralytics import YOLO
from paddleocr import PaddleOCR

from pathlib import Path

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

    if debug:
        print(f"\n{'-' * 50}\n>> STEP 4: EXTRACTED OCR TEXT\n{'-' * 50}")
        print(ocr_text if ocr_text.strip() else "[FATAL ERROR: NO TEXT DETECTED]")
        print("-" * 50)

    if not ocr_text.strip():
        return {"error": "OCR extracted no text."}

    if debug: print(f"[{datetime.now().strftime('%H:%M:%S')}] [5/5] Semantic Parsing (Qwen2.5:7b)...")
    final_payload = parse_with_llm(ocr_text)

    if debug: print(f"[{datetime.now().strftime('%H:%M:%S')}] PIPELINE COMPLETED SUCCESSFULLY.")
    return final_payload