"""A supervised inference process that keeps the v19 models in memory."""
import hashlib
import math
import multiprocessing
import os
import sys
import time
from pathlib import Path

CHECKPOINT_SHA256 = 'a77c370dd3bd0d587e16502332fb6a62efd00223d4ba9f475abda0921798d432'


def serve(pipe, checkpoint, requested_device):
    try:
        if hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest() != CHECKPOINT_SHA256:
            raise ValueError('checkpoint_invalid')
        sys.path.insert(0, '/prediction-model')
        import torch
        from inference import select_device
        from model import load_model
        from preprocessing import (PreprocessingError, load_segmenter, load_rgb_image,
                                   preprocess_photo_encoded, decode_image, to_model_tensor)
        device = select_device(requested_device)
        model = load_model(checkpoint, device)
        segmenter = load_segmenter(device)
        print(f'Inference ready: v19 on {device}', flush=True)
        pipe.send(('ready', str(device)))
    except Exception as error:
        pipe.send(('startup_error', str(error)))
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
            started = time.perf_counter()
            image = load_rgb_image(original)
            encoded = preprocess_photo_encoded(image, segmenter, diagnostics)
            Path(model_input).write_bytes(encoded)
            tensor = to_model_tensor(decode_image(encoded)).to(device)
            if device.type == 'cuda':
                torch.cuda.synchronize(device)
            preprocessing_seconds = time.perf_counter() - started
            started = time.perf_counter()
            with torch.inference_mode():
                result = model(tensor)
            if device.type == 'cuda':
                torch.cuda.synchronize(device)
            prediction_seconds = time.perf_counter() - started
            if result.shape != (1,):
                raise ValueError('invalid prediction shape')
            value = float(result.item())
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('invalid prediction')
            pipe.send(('ok', {'waviness': value, 'preprocessing_seconds': preprocessing_seconds,
                              'prediction_seconds': prediction_seconds}))
        except PreprocessingError as error:
            print(f'Preprocessing failed: {error}', flush=True)
            pipe.send(('no_water', None))
        except Exception as error:
            print(f'Inference failed: {type(error).__name__}: {error}', flush=True)
            pipe.send(('inference_failed', None))


class InferenceService:
    def __init__(self, checkpoint, device='auto', startup_timeout=300, timeout=1800):
        self.checkpoint, self.device = checkpoint, device
        self.startup_timeout, self.timeout = startup_timeout, timeout
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
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        self.process = context.Process(target=serve, args=(child, self.checkpoint, self.device), daemon=True)
        self.pipe = parent
        self.process.start()
        child.close()
        try:
            if not parent.poll(self.startup_timeout):
                raise TimeoutError('inference startup timed out')
            state, detail = parent.recv()
            if state != 'ready':
                raise RuntimeError(f'inference startup failed: {detail}')
        except Exception:
            self.stop()
            raise

    def predict(self, original, model_input, diagnostics):
        try:
            self.start()
            self.pipe.send((str(original), str(model_input), str(diagnostics)))
            if not self.pipe.poll(self.timeout):
                self.stop()
                return 'inference_timeout', None
            return self.pipe.recv()
        except (EOFError, BrokenPipeError, OSError):
            self.stop()
            return 'inference_failed', None
