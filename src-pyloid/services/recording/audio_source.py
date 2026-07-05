"""Audio-source seam.

`AudioSource` is a small protocol so the recorder can be driven by:
  * Production: `SoundDeviceAudioSource` for mic, Windows WASAPI loopback, and
    Linux monitor-source capture.
  * Tests: `FakeAudioSource` (synchronous, deterministic; tests `push()`
    frames; the recorder consumes them via the registered callback).

The contract is intentionally tiny: start with a frame callback, deliver mono
float32 ndarrays at the declared sample rate, stop on request.
"""

from __future__ import annotations

import ctypes
import numpy as np
from typing import Callable, Optional, Protocol
import threading

FrameCallback = Callable[[np.ndarray], None]


class AudioSource(Protocol):
    sample_rate: int
    channels: int

    def start(self, on_frames: FrameCallback) -> None: ...
    def stop(self) -> None: ...


class FakeAudioSource:
    """Test double. Push frames manually; the registered callback receives them."""

    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self.channels = 1
        self._callback: Optional[FrameCallback] = None
        self._stopped = False

    def start(self, on_frames: FrameCallback) -> None:
        self._callback = on_frames
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        self._callback = None

    def push(self, frames: np.ndarray) -> None:
        """Deliver a block of frames to the recorder as if from the audio thread."""
        if self._stopped or self._callback is None:
            return
        if frames.dtype != np.float32:
            frames = frames.astype(np.float32, copy=False)
        self._callback(frames)


class SoundDeviceAudioSource:
    """Production AudioSource backed by sounddevice.

    `loopback=True` on Windows + WASAPI engages WasapiSettings(loopback=True);
    on Linux the loopback flag is ignored — the caller picks a `.monitor`
    device id directly.

    Sample-rate handling: many PortAudio backends (notably JACK and strict
    ALSA setups) refuse to open a stream at an arbitrary rate. We always try
    the target rate first, then fall back to the device's native default rate
    and resample on the fly with `np.interp`.
    """

    def __init__(
        self,
        device_id: int,
        sample_rate: int = 16000,
        loopback: bool = False,
        blocksize: int = 1024,
    ) -> None:
        self.sample_rate = sample_rate  # output rate (what the recorder expects)
        self.channels = 1
        self._device_id = device_id
        self._loopback = loopback
        self._blocksize = blocksize
        self._stream = None  # sounddevice.InputStream
        self._callback: Optional[FrameCallback] = None
        self._device_rate: Optional[int] = None  # the rate the stream actually opens at
        self._resample_phase: float = 0.0  # carries fractional positions across blocks

    def start(self, on_frames: FrameCallback) -> None:
        import sounddevice as sd  # Local import keeps tests free of the C dependency.
        self._callback = on_frames
        self._resample_phase = 0.0

        extra_settings = None
        if self._loopback:
            wasapi = getattr(sd, "WasapiSettings", None)
            if wasapi is not None:
                try:
                    extra_settings = wasapi(loopback=True)
                except TypeError:
                    extra_settings = None

        # Open the stream at the target rate if possible; otherwise fall back
        # to the device's native rate and resample.
        candidate_rates = [self.sample_rate]
        max_input_channels = 1
        max_output_channels = 0
        try:
            info = sd.query_devices(self._device_id)
            native = int(info.get("default_samplerate") or 0)
            if native > 0 and native not in candidate_rates:
                candidate_rates.append(native)
            max_input_channels = max(1, int(info.get("max_input_channels") or 1))
            max_output_channels = int(info.get("max_output_channels") or 0)
        except Exception:
            pass
        # Common fallbacks if everything else fails.
        for r in (48000, 44100):
            if r not in candidate_rates:
                candidate_rates.append(r)

        # Output devices expose their capture channel count through
        # `max_output_channels`; the callback downmixes whatever opens to mono.
        candidate_channels: list[int] = []
        if self._loopback:
            # WASAPI loopback path: prefer output channels, then capture
            # channels, then mono.
            for cand in (max_output_channels, max_input_channels, 1):
                if cand and cand not in candidate_channels:
                    candidate_channels.append(cand)
        else:
            # Mic path: prefer mono, then try the native capture width.
            candidate_channels.append(1)
            if max_input_channels > 1 and max_input_channels not in candidate_channels:
                candidate_channels.append(max_input_channels)

        last_err: Optional[Exception] = None
        for ch in candidate_channels:
            for rate in candidate_rates:
                try:
                    stream = sd.InputStream(
                        device=self._device_id,
                        samplerate=rate,
                        channels=ch,
                        dtype="float32",
                        blocksize=0,  # Let PortAudio pick a backend-compatible size.
                        callback=self._make_pa_callback(rate),
                        extra_settings=extra_settings,
                    )
                    stream.start()
                    self._stream = stream
                    self._device_rate = rate
                    return
                except Exception as exc:
                    last_err = exc
                    continue
        # Nothing worked.
        self._callback = None
        raise RuntimeError(
            f"Could not open audio device {self._device_id}: {last_err}"
        )

    def _make_pa_callback(self, device_rate: int):
        target_rate = self.sample_rate
        do_resample = device_rate != target_rate

        def _cb(indata, _frames, _time, _status):  # noqa: D401 - sd callback
            cb = self._callback
            if cb is None:
                return
            arr = indata
            if arr.ndim == 2 and arr.shape[1] > 1:
                arr = arr.mean(axis=1)
            elif arr.ndim == 2:
                arr = arr[:, 0]
            if arr.dtype != np.float32:
                arr = arr.astype(np.float32, copy=False)
            if do_resample:
                arr = self._resample(arr, device_rate, target_rate)
                if arr.size == 0:
                    return
            cb(arr.copy())  # copy — sounddevice reuses its buffer

        return _cb

    def _resample(self, arr: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        """Linear resampling with phase carry-over so successive blocks join
        cleanly. Good enough for speech going into Whisper."""
        if arr.size == 0:
            return arr
        ratio = dst_rate / src_rate
        first = float(self._resample_phase)
        # Highest output index whose input position is still inside this block:
        # we need first + n/ratio <= arr.size - 1, so n <= (arr.size-1-first)*ratio.
        max_n_float = (arr.size - 1 - first) * ratio
        if max_n_float < 0:
            # Phase is past this block's last sample — produce nothing, advance.
            self._resample_phase = first - arr.size
            return np.zeros(0, dtype=np.float32)
        max_n = int(np.floor(max_n_float))  # may be 0 → still emit one sample
        out_idx = np.arange(max_n + 1, dtype=np.float64)
        in_idx = first + out_idx / ratio
        src_idx = np.arange(arr.size, dtype=np.float64)
        out = np.interp(in_idx, src_idx, arr).astype(np.float32)
        # Absolute input position the NEXT output sample would land at, minus
        # this block's size — i.e. how far into the next block to skip.
        next_first = first + (max_n + 1) / ratio
        self._resample_phase = next_first - arr.size
        return out

    def stop(self) -> None:
        self._callback = None
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._device_rate = None
        self._resample_phase = 0.0


class ParecAudioSource:
    """Linux-only loopback source backed by `parec` (PulseAudio/PipeWire).

    PortAudio on Linux is unreliable for loopback — its ALSA backend can hide
    monitor sources entirely, and its JACK backend renames them by application
    instead of by device. `parec` talks straight to PipeWire / pulse and
    captures the exact `.monitor` source the user picked.
    """

    def __init__(
        self,
        source_name: str,
        sample_rate: int = 16000,
        block_samples: int = 1024,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = 1
        self._source_name = source_name
        self._block_samples = block_samples
        self._proc = None
        self._reader: Optional[object] = None  # threading.Thread when running
        self._stopped = False
        self._callback: Optional[FrameCallback] = None

    def start(self, on_frames: FrameCallback) -> None:
        import shutil
        import subprocess
        import threading
        from services.logger import get_logger

        self._log = get_logger("meeting_audio")

        if shutil.which("parec") is None:
            raise RuntimeError(
                "parec is not installed — required for Linux loopback recording. "
                "Install pulseaudio-utils (pactl/parec ship together)."
            )
        self._callback = on_frames
        self._stopped = False
        # NOTE: do not pass preexec_fn here. We previously used it to set
        # PR_SET_PDEATHSIG so parec would die with the parent — but
        # preexec_fn is documented as unsafe in a multithreaded process
        # (subprocess docs: the child can deadlock before exec if it
        # inherits a held lock from another thread). VoiceFlow has many
        # threads (writer, hotkey, asyncio RPC, transcribe queue, etc.)
        # and that deadlock fired in the field on Bluetooth source
        # selection — wedged the entire asyncio loop and corrupted
        # PipeWire routing. Rely instead on controller.shutdown() (wired
        # to QApplication.aboutToQuit) to kill parec on every normal exit
        # path. Orphan parec on SIGKILL is the accepted trade-off.
        self._proc = subprocess.Popen(
            [
                "parec",
                f"--device={self._source_name}",
                "--raw",
                "--format=float32le",
                f"--rate={self.sample_rate}",
                "--channels=1",
                "--latency-msec=20",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._log.info(
            "parec started",
            source=self._source_name,
            pid=self._proc.pid,
        )
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        # Drain stderr in a separate thread so we see why parec died on its own.
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read_loop(self) -> None:
        block_bytes = self._block_samples * 4  # float32
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while not self._stopped:
            try:
                data = proc.stdout.read(block_bytes)
            except Exception as exc:
                if hasattr(self, "_log"):
                    self._log.warning("parec read failed", error=str(exc))
                break
            if not data:
                # parec closed its pipe — almost always means the subprocess
                # exited (often because the source went away or pulse refused).
                if hasattr(self, "_log") and not self._stopped:
                    exit_code = proc.poll()
                    self._log.warning(
                        "parec stream ended",
                        exit_code=exit_code,
                        source=self._source_name,
                    )
                break
            cb = self._callback
            if cb is None:
                continue
            arr = np.frombuffer(data, dtype=np.float32)
            if arr.size == 0:
                continue
            cb(arr.copy())

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                if self._stopped:
                    return
                line = raw.decode("utf-8", "replace").strip()
                if line and hasattr(self, "_log"):
                    self._log.warning("parec stderr", line=line)
        except Exception:
            pass

    def stop(self) -> None:
        self._stopped = True
        self._callback = None
        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=1.0)
                except Exception:
                    self._proc.kill()
            except Exception:
                pass
            self._proc = None


class ScreenCaptureKitAudioSource:
    """macOS system-audio source backed by ScreenCaptureKit."""

    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self.channels = 1
        self._callback: Optional[FrameCallback] = None
        self._stream = None
        self._output = None
        self._queue = None
        self._resample_phase: float = 0.0
        self._lock = threading.Lock()
        self._stopped = True
        self._log = None

    def start(self, on_frames: FrameCallback) -> None:
        from services.macos_permissions import (
            get_screen_recording_permission_status,
            request_screen_recording_permission,
        )
        from services.logger import get_logger

        self._log = get_logger("meeting_audio")
        status = get_screen_recording_permission_status()
        if status != "granted":
            status = request_screen_recording_permission()
        if status != "granted":
            raise PermissionError(
                "Screen Recording permission is required for macOS system audio."
            )

        ScreenCaptureKit, _ = _load_screencapturekit_modules()
        self._callback = on_frames
        self._resample_phase = 0.0
        self._stopped = False

        content = self._get_shareable_content(ScreenCaptureKit)
        displays = list(content.displays() or [])
        if not displays:
            raise RuntimeError("No display is available for ScreenCaptureKit capture.")

        content_filter = ScreenCaptureKit.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
            displays[0],
            [],
        )
        configuration = ScreenCaptureKit.SCStreamConfiguration.alloc().init()
        _set_objc_value(configuration, "setCapturesAudio_", True)
        _set_objc_value(configuration, "setExcludesCurrentProcessAudio_", True)
        _set_objc_value(configuration, "setSampleRate_", int(self.sample_rate))
        _set_objc_value(configuration, "setChannelCount_", 2)
        _set_objc_value(configuration, "setWidth_", 2)
        _set_objc_value(configuration, "setHeight_", 2)
        _set_objc_value(configuration, "setQueueDepth_", 1)

        self._output = self._make_stream_output()
        self._queue = _make_dispatch_queue("io.github.infiniv.VoiceFlow.screencapturekit.audio")
        self._stream = ScreenCaptureKit.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter,
            configuration,
            self._output,
        )
        output_type = getattr(ScreenCaptureKit, "SCStreamOutputTypeAudio")
        _add_stream_output(self._stream, self._output, output_type, self._queue)
        _call_completion(self._stream.startCaptureWithCompletionHandler_)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._callback = None
            stream = self._stream
            self._stream = None
            self._output = None
            self._queue = None
            self._resample_phase = 0.0
        if stream is not None:
            try:
                _call_completion(stream.stopCaptureWithCompletionHandler_, timeout_s=3.0)
            except Exception:
                if self._log:
                    self._log.exception("ScreenCaptureKit stop failed")

    def _get_shareable_content(self, ScreenCaptureKit):
        event = threading.Event()
        result = {"content": None, "error": None}

        def _handler(content, error):
            result["content"] = content
            result["error"] = error
            event.set()

        ScreenCaptureKit.SCShareableContent.getShareableContentWithCompletionHandler_(
            _handler
        )
        if not event.wait(timeout=10.0):
            raise TimeoutError("Timed out while listing ScreenCaptureKit content.")
        if result["error"] is not None:
            raise RuntimeError(str(result["error"]))
        if result["content"] is None:
            raise RuntimeError("ScreenCaptureKit returned no shareable content.")
        return result["content"]

    def _make_stream_output(self):
        from Foundation import NSObject  # type: ignore

        owner = self

        class _StreamOutput(NSObject):
            def stream_didOutputSampleBuffer_ofType_(self, _stream, sample_buffer, output_type):
                owner._handle_sample_buffer(sample_buffer, output_type)

            def stream_didStopWithError_(self, _stream, error):
                if owner._log:
                    owner._log.warning("ScreenCaptureKit stream stopped", error=str(error))

        return _StreamOutput.alloc().init()

    def _handle_sample_buffer(self, sample_buffer, _output_type) -> None:
        with self._lock:
            if self._stopped:
                return
            cb = self._callback
        if cb is None:
            return
        try:
            arr = _sample_buffer_to_mono_float32(sample_buffer)
            if arr.size == 0:
                return
            src_rate = int(getattr(arr, "_voiceflow_sample_rate", self.sample_rate))
            if src_rate != self.sample_rate:
                arr = self._resample(arr, src_rate, self.sample_rate)
            if arr.size:
                cb(arr.copy())
        except Exception as exc:
            if self._log:
                self._log.warning("ScreenCaptureKit audio buffer skipped", error=str(exc))

    def _resample(self, arr: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if arr.size == 0 or src_rate <= 0 or dst_rate <= 0:
            return np.zeros(0, dtype=np.float32)
        ratio = dst_rate / src_rate
        first = float(self._resample_phase)
        max_n_float = (arr.size - 1 - first) * ratio
        if max_n_float < 0:
            self._resample_phase = first - arr.size
            return np.zeros(0, dtype=np.float32)
        max_n = int(np.floor(max_n_float))
        out_idx = np.arange(max_n + 1, dtype=np.float64)
        in_idx = first + out_idx / ratio
        src_idx = np.arange(arr.size, dtype=np.float64)
        out = np.interp(in_idx, src_idx, arr).astype(np.float32)
        next_first = first + (max_n + 1) / ratio
        self._resample_phase = next_first - arr.size
        return out


def _load_screencapturekit_modules():
    try:
        import ScreenCaptureKit  # type: ignore
        import CoreMedia  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "ScreenCaptureKit PyObjC frameworks are not available."
        ) from exc
    return ScreenCaptureKit, CoreMedia


def _set_objc_value(obj, setter: str, value) -> None:
    method = getattr(obj, setter, None)
    if method is not None:
        method(value)


def _make_dispatch_queue(label: str):
    try:
        import dispatch  # type: ignore

        return dispatch.dispatch_queue_create(label, None)
    except Exception:
        return None


def _add_stream_output(stream, output, output_type, queue) -> None:
    method = getattr(stream, "addStreamOutput_type_sampleHandlerQueue_error_", None)
    if method is not None:
        result = method(output, output_type, queue, None)
    else:
        method = getattr(stream, "addStreamOutput_type_sampleHandlerQueue_")
        result = method(output, output_type, queue)

    if isinstance(result, tuple):
        ok = bool(result[0])
        error = result[1] if len(result) > 1 else None
    else:
        ok = result is None or bool(result)
        error = None
    if not ok:
        raise RuntimeError(f"Could not attach ScreenCaptureKit audio output: {error}")


def _call_completion(method, timeout_s: float = 10.0) -> None:
    event = threading.Event()
    result = {"error": None}

    def _handler(error=None):
        result["error"] = error
        event.set()

    method(_handler)
    if not event.wait(timeout=timeout_s):
        raise TimeoutError("Timed out waiting for ScreenCaptureKit.")
    if result["error"] is not None:
        raise RuntimeError(str(result["error"]))


def _sample_buffer_to_mono_float32(sample_buffer) -> np.ndarray:
    import CoreMedia  # type: ignore
    import CoreAudio  # type: ignore

    if not CoreMedia.CMSampleBufferIsValid(sample_buffer):
        return np.zeros(0, dtype=np.float32)

    desc = CoreMedia.CMSampleBufferGetFormatDescription(sample_buffer)
    asbd = CoreMedia.CMAudioFormatDescriptionGetStreamBasicDescription(desc)
    channels = max(1, int(_asbd_value(asbd, "mChannelsPerFrame", 2)))
    bits = int(_asbd_value(asbd, "mBitsPerChannel", 32))
    flags = int(_asbd_value(asbd, "mFormatFlags", getattr(CoreAudio, "kAudioFormatFlagIsFloat", 1)))
    sample_rate = int(_asbd_value(asbd, "mSampleRate", 16000))
    bytes_per_frame = int(_asbd_value(asbd, "mBytesPerFrame", max(1, bits // 8) * channels))

    non_interleaved = bool(flags & getattr(CoreAudio, "kAudioFormatFlagIsNonInterleaved", 0))
    if non_interleaved:
        buffers = _copy_sample_buffer_audio_buffers(
            sample_buffer,
            CoreMedia,
            CoreAudio,
            buffer_count=channels,
        )
        return _audio_buffers_to_mono_float32(
            buffers,
            bits,
            flags,
            sample_rate,
            bytes_per_frame,
            CoreAudio,
        )

    payload = _copy_sample_buffer_block_bytes(sample_buffer, CoreMedia)
    if payload is None:
        buffers = _copy_sample_buffer_audio_buffers(
            sample_buffer,
            CoreMedia,
            CoreAudio,
            buffer_count=1,
        )
        return _audio_buffers_to_mono_float32(
            buffers,
            bits,
            flags,
            sample_rate,
            bytes_per_frame,
            CoreAudio,
        )

    arr = _pcm_payload_to_float32(
        payload,
        bits,
        flags,
        channels,
        bytes_per_frame,
        CoreAudio,
    )
    return _with_sample_rate(arr, sample_rate)


def _copy_sample_buffer_block_bytes(sample_buffer, CoreMedia) -> Optional[bytes]:
    block = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
    if block is None:
        return None

    length = int(CoreMedia.CMBlockBufferGetDataLength(block))
    if length <= 0:
        return b""

    raw = bytearray(length)
    status = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, raw)
    if isinstance(status, tuple):
        status = status[0]
    if status not in (0, None):
        raise RuntimeError(f"CMBlockBufferCopyDataBytes failed: {status}")
    return bytes(raw)


def _copy_sample_buffer_audio_buffers(
    sample_buffer,
    CoreMedia,
    CoreAudio,
    buffer_count: int,
) -> list[tuple[bytes, int]]:
    method = getattr(
        CoreMedia,
        "CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer",
        None,
    )
    audio_buffer_list = getattr(CoreAudio, "AudioBufferList", None)
    if method is None or audio_buffer_list is None:
        return []

    requested_count = max(1, int(buffer_count))
    size = _audio_buffer_list_size(requested_count)
    flags = getattr(CoreMedia, "kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment", 0)
    try:
        result = method(sample_buffer, None, None, 0, None, None, flags, None)
        _status, needed_size, _block_buffer = _audio_buffer_list_result(result)
        if needed_size and int(needed_size) > size:
            size = int(needed_size)
            requested_count = max(requested_count, _audio_buffer_count_for_size(size))
    except Exception:
        pass

    buffers = audio_buffer_list(requested_count)
    result = method(sample_buffer, None, buffers, size, None, None, flags, None)
    status, _needed_size, _block_buffer = _audio_buffer_list_result(result)
    if status not in (0, None):
        return []

    payloads: list[tuple[bytes, int]] = []
    for index in range(len(buffers)):
        audio_buffer = buffers[index]
        byte_size = int(getattr(audio_buffer, "mDataByteSize", 0) or 0)
        if byte_size <= 0:
            continue
        data = getattr(audio_buffer, "mData", None)
        payload = _audio_buffer_data_to_bytes(data, byte_size)
        if payload:
            channels = max(1, int(getattr(audio_buffer, "mNumberChannels", 1) or 1))
            payloads.append((payload, channels))

    return payloads


def _audio_buffer_list_result(result) -> tuple[object, Optional[int], object]:
    if isinstance(result, tuple):
        status = result[0] if result else None
        needed_size = result[1] if len(result) > 1 else None
        block_buffer = result[2] if len(result) > 2 else None
        return status, needed_size, block_buffer
    return result, None, None


def _audio_buffer_list_size(buffer_count: int) -> int:
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    audio_buffer_size = ctypes.sizeof(ctypes.c_uint32) * 2 + pointer_size
    header_size = ctypes.sizeof(ctypes.c_uint32)
    first_buffer_offset = ((header_size + pointer_size - 1) // pointer_size) * pointer_size
    return first_buffer_offset + audio_buffer_size * max(1, int(buffer_count))


def _audio_buffer_count_for_size(buffer_list_size: int) -> int:
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    audio_buffer_size = ctypes.sizeof(ctypes.c_uint32) * 2 + pointer_size
    header_size = ctypes.sizeof(ctypes.c_uint32)
    first_buffer_offset = ((header_size + pointer_size - 1) // pointer_size) * pointer_size
    if buffer_list_size <= first_buffer_offset:
        return 1
    return max(
        1,
        int(np.ceil((buffer_list_size - first_buffer_offset) / audio_buffer_size)),
    )


def _audio_buffer_data_to_bytes(data, byte_size: int) -> bytes:
    if data is None or byte_size <= 0:
        return b""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data[:byte_size])
    try:
        return memoryview(data).tobytes()[:byte_size]
    except TypeError:
        pass
    as_buffer = getattr(data, "as_buffer", None)
    if callable(as_buffer):
        return bytes(as_buffer(byte_size))[:byte_size]
    pointer = None
    pointer_as_integer = getattr(data, "pointerAsInteger", None)
    if callable(pointer_as_integer):
        pointer = pointer_as_integer()
    elif isinstance(data, int):
        pointer = data
    if pointer:
        return ctypes.string_at(pointer, byte_size)
    return b""


def _audio_buffers_to_mono_float32(
    buffers: list[tuple[bytes, int]],
    bits: int,
    flags: int,
    sample_rate: int,
    bytes_per_frame: int,
    CoreAudio,
) -> np.ndarray:
    arrays = [
        _pcm_payload_to_float32(
            payload,
            bits,
            flags,
            channels,
            bytes_per_frame,
            CoreAudio,
        )
        for payload, channels in buffers
    ]
    arrays = [arr for arr in arrays if arr.size > 0]
    if not arrays:
        return np.zeros(0, dtype=np.float32)
    if len(arrays) == 1:
        return _with_sample_rate(arrays[0], sample_rate)

    length = min(arr.size for arr in arrays)
    if length <= 0:
        return np.zeros(0, dtype=np.float32)
    mixed = np.vstack([arr[:length] for arr in arrays]).mean(axis=0).astype(np.float32)
    return _with_sample_rate(mixed, sample_rate)


def _pcm_payload_to_float32(
    payload: bytes,
    bits: int,
    flags: int,
    channels: int,
    bytes_per_frame: int,
    CoreAudio,
) -> np.ndarray:
    channels = max(1, int(channels))
    sample_width = max(1, int(bits) // 8)
    bytes_per_frame = max(1, int(bytes_per_frame))
    expected_frame_width = sample_width * channels
    frame_count = len(payload) // bytes_per_frame
    usable = frame_count * bytes_per_frame
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)

    if bytes_per_frame == expected_frame_width:
        sample_bytes = payload[:usable]
    else:
        sample_bytes = bytearray(frame_count * expected_frame_width)
        for frame_index in range(frame_count):
            src = frame_index * bytes_per_frame
            dst = frame_index * expected_frame_width
            sample_bytes[dst:dst + expected_frame_width] = payload[
                src:src + expected_frame_width
            ]
        sample_bytes = bytes(sample_bytes)

    arr = _typed_pcm_bytes_to_float32(sample_bytes, bits, flags, CoreAudio)
    if channels > 1:
        usable_samples = (arr.size // channels) * channels
        arr = arr[:usable_samples].reshape((-1, channels)).mean(axis=1)
    return arr.astype(np.float32, copy=False)


def _typed_pcm_bytes_to_float32(payload: bytes, bits: int, flags: int, CoreAudio) -> np.ndarray:
    big_endian = bool(flags & getattr(CoreAudio, "kAudioFormatFlagIsBigEndian", 2))
    endian = ">" if big_endian else "<"
    is_float = bool(flags & getattr(CoreAudio, "kAudioFormatFlagIsFloat", 1))
    is_signed = bool(flags & getattr(CoreAudio, "kAudioFormatFlagIsSignedInteger", 4))

    if is_float and bits == 32:
        return np.frombuffer(payload, dtype=np.dtype(f"{endian}f4")).astype(np.float32)
    elif is_float and bits == 64:
        return np.frombuffer(payload, dtype=np.dtype(f"{endian}f8")).astype(np.float32)
    elif is_signed and bits == 16:
        return np.frombuffer(payload, dtype=np.dtype(f"{endian}i2")).astype(np.float32) / 32768.0
    elif is_signed and bits == 32:
        return np.frombuffer(payload, dtype=np.dtype(f"{endian}i4")).astype(np.float32) / 2147483648.0
    elif not is_signed and bits == 8:
        return (np.frombuffer(payload, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif not is_signed and bits == 16:
        return (
            np.frombuffer(payload, dtype=np.dtype(f"{endian}u2")).astype(np.float32)
            - 32768.0
        ) / 32768.0
    raise RuntimeError(f"Unsupported ScreenCaptureKit audio format: {bits}-bit flags={flags}")


def _asbd_value(asbd, name: str, default):
    try:
        value = getattr(asbd, name)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if isinstance(asbd, dict) and name in asbd:
            return asbd[name]
    except Exception:
        pass
    return default


def _with_sample_rate(arr: np.ndarray, sample_rate: int) -> np.ndarray:
    class _AudioArray(np.ndarray):
        pass

    out = np.asarray(arr, dtype=np.float32).view(_AudioArray)
    out._voiceflow_sample_rate = sample_rate
    return out
