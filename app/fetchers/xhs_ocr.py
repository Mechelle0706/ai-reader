"""Run local Chinese OCR for Xiaohongshu note images."""

_engine = None
_engine_initialized = False
_engine_error: str | None = None


def _get_engine():
    global _engine, _engine_initialized, _engine_error

    if not _engine_initialized:
        _engine_initialized = True
        try:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        except Exception as exc:
            _engine_error = str(exc)

    if _engine is None:
        raise RuntimeError(_engine_error or "RapidOCR 初始化失败。")
    return _engine


def _reading_order(results) -> str:
    def _position(item):
        points = item[0]
        return (
            sum(point[1] for point in points) / len(points),
            sum(point[0] for point in points) / len(points),
        )

    texts = [str(item[1]).strip() for item in sorted(results or [], key=_position)]
    return "\n".join(text for text in texts if text)


def recognize_image_bytes(image_bytes: bytes, engine=None) -> str:
    """Recognize text in an encoded image, ordered top-to-bottom then left-to-right."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法解码图片。")

    active_engine = engine if engine is not None else _get_engine()
    result, _ = active_engine(image)
    return _reading_order(result)