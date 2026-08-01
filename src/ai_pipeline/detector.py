import numpy as np
from typing import Optional, Tuple
from ultralytics import YOLO

def detect_receipt(image_cv: np.ndarray, model: YOLO, conf_threshold: float = 0.5) -> Tuple[Optional[np.ndarray], float]:
    """
    Detects the receipt bounding box and returns its 4 spatial coordinates.
    """
    results = model.predict(image_cv, conf=conf_threshold, verbose=False)
    obb = getattr(results[0], "obb", None)

    if obb is not None and len(obb) > 0:
        # Extract the box with the highest confidence score
        best_idx = int(obb.conf.argmax().cpu().numpy())
        points = obb.xyxyxyxy[best_idx].cpu().numpy()
        confidence = float(obb.conf[best_idx].cpu().numpy())
        return points, confidence

    return None, 0.0