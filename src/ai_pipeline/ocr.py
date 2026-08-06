import numpy as np

def reconstruct_layout_text(ocr_results, min_conf: float = 0.40) -> str:
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

    return "\n".join(
        "   ".join(f["text"] for f in sorted(line, key=lambda f: f["x"]))
        for line in lines
    )


def _normalize_paddle_result(raw_results):
    """Convert PaddleOCR 3.x predict() output into 2.x-like [bbox, (text, conf)]."""
    if not raw_results:
        return []

    first = raw_results[0]

    if isinstance(first, (list, tuple)) and first and isinstance(first[0], (list, tuple)):
        return first

    data = first
    if hasattr(first, "keys"):
        data = first
    elif hasattr(first, "json"):
        data = first.json
        if isinstance(data, dict) and "res" in data:
            data = data["res"]

    texts = data.get("rec_texts") if hasattr(data, "get") else None
    scores = data.get("rec_scores") if hasattr(data, "get") else None
    polys = data.get("rec_polys") if hasattr(data, "get") else None
    if polys is None and hasattr(data, "get"):
        polys = data.get("dt_polys")

    if texts is None:
        return []

    lines = []
    for bbox, text, conf in zip(polys, texts, scores):
        lines.append([bbox.tolist() if hasattr(bbox, "tolist") else bbox, (str(text), float(conf))])
    return lines


def execute_ocr(paddle_reader, image_cv) -> str:
    if image_cv.dtype != np.uint8:
        image_cv = np.clip(image_cv, 0, 255).astype(np.uint8)
    image_cv = np.ascontiguousarray(image_cv)

    if hasattr(paddle_reader, "predict"):
        raw_results = paddle_reader.predict(image_cv)
    else:
        raw_results = paddle_reader.ocr(image_cv)

    lines = _normalize_paddle_result(raw_results)
    if not lines:
        return ""
    return reconstruct_layout_text(lines)