import numpy as np
from typing import List, Any

def reconstruct_layout_text(ocr_results: List[Any], min_conf: float = 0.40) -> str:
    """Groups disjointed OCR bounding boxes into cohesive horizontal text lines."""
    fragments = []

    for item in ocr_results:
        if not item or len(item) != 2:
            continue

        bbox, text_data = item[0], item[1]
        text, conf = str(text_data[0]).strip(), float(text_data[1])

        if not text or conf < min_conf:
            continue

        ys = [float(p[1]) for p in bbox]
        xs = [float(p[0]) for p in bbox]
        fragments.append({"text": text, "y": sum(ys)/4, "x": min(xs), "h": max(ys)-min(ys)})

    if not fragments:
        return ""

    fragments.sort(key=lambda f: f["y"])
    lines, current_line = [], [fragments[0]]

    for frag in fragments[1:]:
        avg_y = sum(f["y"] for f in current_line) / len(current_line)
        if abs(frag["y"] - avg_y) <= (frag["h"] * 0.6):
            current_line.append(frag)
        else:
            lines.append(current_line)
            current_line = [frag]
    lines.append(current_line)

    return "\n".join("   ".join(f["text"] for f in sorted(line, key=lambda f: f["x"])) for line in lines)

def execute_ocr(paddle_reader, image_cv: np.ndarray) -> str:
    """Executes the PaddleOCR engine and returns the unified string matrix."""
    if image_cv.dtype != np.uint8:
        image_cv = np.clip(image_cv, 0, 255).astype(np.uint8)
    image_cv = np.ascontiguousarray(image_cv)

    raw_results = paddle_reader.ocr(image_cv, cls=True)

    if not raw_results or not raw_results[0]:
        return ""

    return reconstruct_layout_text(raw_results[0])