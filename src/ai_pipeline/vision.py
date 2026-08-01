import cv2
import numpy as np

def order_points_robust(pts: np.ndarray) -> np.ndarray:
    """Sorts spatial coordinates into strict TL, TR, BR, BL order."""
    pts = np.array(pts, dtype="float32")
    x_sorted = pts[np.argsort(pts[:, 0]), :]

    left_most, right_most = x_sorted[:2, :], x_sorted[2:, :]
    left_most = left_most[np.argsort(left_most[:, 1]), :]
    right_most = right_most[np.argsort(right_most[:, 1]), :]

    return np.array([left_most[0], right_most[0], right_most[1], left_most[1]], dtype="float32")

def process_crop(image_cv: np.ndarray, points: np.ndarray, padding: float = 0.02) -> np.ndarray:
    """Applies a homography matrix to flatten the 3D perspective of the document."""
    rect = order_points_robust(points)
    (tl, tr, br, bl) = rect

    width = max(int(np.linalg.norm(tr - tl)), int(np.linalg.norm(br - bl)))
    height = max(int(np.linalg.norm(tl - bl)), int(np.linalg.norm(tr - br)))

    pad_x, pad_y = int(width * padding), int(height * padding)
    dst = np.array([
        [pad_x, pad_y], [width + pad_x - 1, pad_y],
        [width + pad_x - 1, height + pad_y - 1], [pad_x, height + pad_y - 1]
    ], dtype="float32")

    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image_cv, matrix, (width + 2*pad_x, height + 2*pad_y))

def enforce_portrait_orientation(image_cv: np.ndarray) -> np.ndarray:
    """Forces vertical orientation. Essential for OCR horizontal layout parsing."""
    height, width = image_cv.shape[:2]
    return cv2.rotate(image_cv, cv2.ROTATE_90_CLOCKWISE) if width > height else image_cv

def _get_odd_kernel_size(base_size: float, min_size: int = 3) -> int:
    size = max(min_size, int(base_size))
    return size if size % 2 == 1 else size + 1

def convert_to_grayscale(image_cv: np.ndarray) -> np.ndarray:
    if image_cv.ndim == 3 and image_cv.shape[2] == 3:
        return cv2.cvtColor(image_cv, cv2.COLOR_BGR2GRAY)
    return image_cv.copy()

def scale_image_width(gray_image: np.ndarray, min_width: int = 1200, max_width: int = 2400) -> np.ndarray:
    height, width = gray_image.shape[:2]
    if width < min_width:
        scale = min_width / width
        return cv2.resize(gray_image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_CUBIC)
    elif width > max_width:
        scale = max_width / width
        return cv2.resize(gray_image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    return gray_image

def photometric_dewarp_division(gray_image: np.ndarray) -> np.ndarray:
    bg_kernel = _get_odd_kernel_size(min(gray_image.shape[:2]) * 0.045, 31)
    background_shadow_map = cv2.medianBlur(gray_image, bg_kernel)
    return cv2.divide(gray_image, background_shadow_map, scale=255)

def apply_clahe(gray_image: np.ndarray, clip_limit: float = 1.6) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(8, 8))
    return clahe.apply(gray_image)

def convert_gray_to_bgr(gray_image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(gray_image, cv2.COLOR_GRAY2BGR)

def preprocess_for_ocr(image_cv: np.ndarray) -> np.ndarray:
    img = convert_to_grayscale(image_cv)
    img = scale_image_width(img, min_width=1200, max_width=2400)
    img = photometric_dewarp_division(img)
    img = apply_clahe(img, clip_limit=2.0)
    img = convert_gray_to_bgr(img)
    return img