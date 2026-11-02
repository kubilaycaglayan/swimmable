"""Supervised, warm ONNX Runtime service for the pinned fast-v2.2 release."""
import hashlib
import multiprocessing
import os
import time
from pathlib import Path

REGRESSOR_SHA256 = "a1a8f65500d88debe2980c900ccc55b2b8b6d46f6492d3b3ddf2a6b7ad3eb8e4"
SEGMENTER_SHA256 = "96410800203e3f19b7dab26980ea6413f6f9369bb868e23bfa7f14eca6681fda"
OUTPUT_SIZE = (224, 224)
SEGMENTATION_SIZE = (448, 448)
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
class PreprocessingError(RuntimeError):
    """Raised when a reliable lower-water crop cannot be selected."""


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_runtime(regressor_path, segmenter_path, threads, device="auto"):
    import onnxruntime as ort
    from .crop_selector import warm_crop_selector

    if sha256(regressor_path) != REGRESSOR_SHA256:
        raise ValueError("regressor_invalid")
    if sha256(segmenter_path) != SEGMENTER_SHA256:
        raise ValueError("segmenter_invalid")
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    if device not in ("auto", "cuda", "cpu"):
        raise ValueError("INFERENCE_DEVICE must be auto, cuda, or cpu")

    def initialize(providers, require_cuda):
        regressor = ort.InferenceSession(str(regressor_path), sess_options=options, providers=providers)
        segmenter = ort.InferenceSession(str(segmenter_path), sess_options=options, providers=providers)
        if require_cuda and (
            "CUDAExecutionProvider" not in regressor.get_providers()
            or "CUDAExecutionProvider" not in segmenter.get_providers()
        ):
            raise RuntimeError("CUDA provider did not initialize for both models")
        if regressor.get_inputs()[0].name != "images" or regressor.get_outputs()[0].name != "scores":
            raise ValueError("regressor_contract_invalid")
        if segmenter.get_inputs()[0].name != "pixel_values" or segmenter.get_outputs()[0].name != "water_mask":
            raise ValueError("segmenter_contract_invalid")
        # Warm kernels and crop selection before accepting any queued photos.
        import numpy as np
        segmenter.run(["water_mask"], {"pixel_values": np.zeros((1, 3, 448, 448), dtype=np.float32)})
        regressor.run(["scores"], {"images": np.zeros((1, 3, 224, 224), dtype=np.float32)})
        crop_warmup_seconds = warm_crop_selector()
        print(f"Inference graph and crop-selector warm-up complete ({crop_warmup_seconds:.3f}s)", flush=True)
        return regressor, segmenter

    if device in ("auto", "cuda"):
        try:
            if "CUDAExecutionProvider" not in ort.get_available_providers():
                raise RuntimeError("CUDAExecutionProvider is unavailable")
            # The pip GPU distribution supplies CUDA/cuDNN shared libraries.
            ort.preload_dlls()
            regressor, segmenter = initialize(["CUDAExecutionProvider", "CPUExecutionProvider"], True)
            selected_device = "cuda"
        except Exception:
            if device == "cuda":
                raise
            print("CUDA inference setup or warm-up failed; falling back to CPU", flush=True)
            import gc
            regressor = segmenter = None
            gc.collect()
            regressor, segmenter = initialize(["CPUExecutionProvider"], False)
            selected_device = "cpu"
    else:
        regressor, segmenter = initialize(["CPUExecutionProvider"], False)
        selected_device = "cpu"
    return ort, regressor, segmenter, selected_device


def predict_photo(original, model_input, diagnostics, regressor, segmenter):
    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    from .crop_selector import select_crop

    started = time.perf_counter()
    with Image.open(original) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    source = np.asarray(image, dtype=np.uint8)
    resized = cv2.resize(
        source,
        SEGMENTATION_SIZE,
        interpolation=cv2.INTER_AREA if max(source.shape[:2]) > 448 else cv2.INTER_LINEAR_EXACT,
    ).astype(np.float32)
    pixels = resized * np.float32(1.0 / 255.0)
    pixels = (pixels - np.asarray(MEAN, dtype=np.float32)) / np.asarray(STD, dtype=np.float32)
    tensor = np.ascontiguousarray(pixels.transpose(2, 0, 1)[None], dtype=np.float32)
    water_448 = segmenter.run(["water_mask"], {"pixel_values": tensor})[0][0].astype(bool)
    water_224 = cv2.resize(
        water_448.astype(np.uint8), OUTPUT_SIZE, interpolation=cv2.INTER_NEAREST_EXACT
    ).astype(bool)
    if diagnostics:
        diagnostic_dir = Path(diagnostics)
        diagnostic_dir.mkdir(parents=True, exist_ok=True)
        Image.fromarray(water_448.astype(np.uint8) * 255, mode="L").save(diagnostic_dir / "water-mask-448.png")
        Image.fromarray(water_224.astype(np.uint8) * 255, mode="L").save(diagnostic_dir / "selector-mask-224.png")
    selected = select_crop(water_224, np.zeros(OUTPUT_SIZE, dtype=bool))
    if selected is None:
        raise PreprocessingError("No reliable lower-water crop could be selected from this photo.")

    left, top, right, bottom = selected[0]
    source_height, source_width = source.shape[:2]
    x0 = max(0, min(source_width, int(np.floor(left * source_width / 224))))
    x1 = max(x0 + 1, min(source_width, int(np.ceil(right * source_width / 224))))
    y0 = max(0, min(source_height, int(np.floor(top * source_height / 224))))
    y1 = max(y0 + 1, min(source_height, int(np.ceil(bottom * source_height / 224))))
    source_roi = source[y0:y1, x0:x1]
    mask_roi = water_224[top:bottom, left:right]
    if diagnostics:
        Image.fromarray(source_roi, mode="RGB").save(Path(diagnostics) / "selected-source-region.jpg", quality=95)

    scale = min(224 / source_roi.shape[1], 224 / source_roi.shape[0])
    resized_width = max(1, min(224, round(source_roi.shape[1] * scale)))
    resized_height = max(1, min(224, round(source_roi.shape[0] * scale)))
    offset_x, offset_y = (224 - resized_width) // 2, (224 - resized_height) // 2
    canvas = np.zeros((224, 224, 3), dtype=np.uint8)
    canvas_roi = canvas[offset_y:offset_y + resized_height, offset_x:offset_x + resized_width]
    cv2.resize(source_roi, (resized_width, resized_height), dst=canvas_roi, interpolation=cv2.INTER_LANCZOS4)
    final_mask = cv2.resize(
        mask_roi.astype(np.uint8), (resized_width, resized_height), interpolation=cv2.INTER_NEAREST_EXACT
    )
    canvas_roi[final_mask == 0] = 0

    encoded = __import__("io").BytesIO()
    Image.fromarray(canvas, mode="RGB").save(encoded, format="JPEG", quality=95, subsampling=0)
    encoded_bytes = encoded.getvalue()
    with Image.open(__import__("io").BytesIO(encoded_bytes)) as decoded:
        model_pixels = np.asarray(decoded.convert("RGB"), dtype=np.uint8).astype(np.float32) / np.float32(255.0)
    mean = np.asarray(MEAN, dtype=np.float32).reshape(1, 1, 3)
    std = np.asarray(STD, dtype=np.float32).reshape(1, 1, 3)
    model_tensor = np.ascontiguousarray(((model_pixels - mean) / std).transpose(2, 0, 1)[None], dtype=np.float32)
    if diagnostics:
        (Path(diagnostics) / "final-model-input.jpg").write_bytes(encoded_bytes)
    Path(model_input).write_bytes(encoded_bytes)
    preprocessing_seconds = time.perf_counter() - started

    started = time.perf_counter()
    output = regressor.run(["scores"], {"images": model_tensor})[0]
    prediction_seconds = time.perf_counter() - started
    if output.shape != (1,) or not np.isfinite(output[0]) or not 0 <= float(output[0]) <= 1:
        raise ValueError("invalid_prediction")
    return {
        "waviness": float(output[0]),
        "preprocessing_seconds": preprocessing_seconds,
        "prediction_seconds": prediction_seconds,
    }


def serve(pipe, regressor_path, segmenter_path, threads, device):
    try:
        import cv2
        from pillow_heif import register_heif_opener

        register_heif_opener()
        cv2.setNumThreads(4)
        ort, regressor, segmenter, selected_device = load_runtime(regressor_path, segmenter_path, threads, device)
        print(
            f"Inference ready: fast-v2.2 ONNX Runtime {ort.__version__} on {selected_device} "
            f"({threads} intra-op threads)",
            flush=True,
        )
        pipe.send(("ready", {"device": selected_device, "runtime": ort.__version__}))
    except Exception as error:
        pipe.send(("startup_error", f"{type(error).__name__}: {error}"))
        return

    while True:
        try:
            request = pipe.recv()
        except EOFError:
            return
        if request is None:
            return
        original, model_input, diagnostics = request
        try:
            result = predict_photo(original, model_input, diagnostics, regressor, segmenter)
            pipe.send(("ok", result))
        except PreprocessingError as error:
            print(f"Preprocessing failed: {error}", flush=True)
            pipe.send(("no_water", None))
        except Exception as error:
            print(f"Inference failed: {type(error).__name__}: {error}", flush=True)
            pipe.send(("inference_failed", None))


class InferenceService:
    def __init__(self, regressor, segmenter, startup_timeout=300, timeout=1800, threads=4, device="auto"):
        self.regressor, self.segmenter = str(regressor), str(segmenter)
        self.startup_timeout, self.timeout, self.threads, self.device = startup_timeout, timeout, threads, device
        self.process = self.pipe = None

    def stop(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=10)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()
            self.pipe.close()
        self.process = self.pipe = None

    def start(self):
        if self.process is not None and self.process.is_alive():
            return
        self.stop()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self.process = context.Process(
            target=serve,
            args=(child, self.regressor, self.segmenter, self.threads, self.device),
            daemon=True,
        )
        self.pipe = parent
        self.process.start()
        child.close()
        try:
            if not parent.poll(self.startup_timeout):
                raise TimeoutError("inference startup timed out")
            state, detail = parent.recv()
            if state != "ready":
                raise RuntimeError(f"inference startup failed: {detail}")
        except Exception:
            self.stop()
            raise

    def predict(self, original, model_input, diagnostics):
        try:
            self.start()
            self.pipe.send((str(original), str(model_input), str(diagnostics)))
            if not self.pipe.poll(self.timeout):
                self.stop()
                return "inference_timeout", None
            return self.pipe.recv()
        except (EOFError, BrokenPipeError, OSError):
            self.stop()
            return "inference_failed", None
