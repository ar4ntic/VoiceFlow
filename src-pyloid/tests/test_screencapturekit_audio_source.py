import sys
import types

import numpy as np

from services.recording.audio_source import (
    _audio_buffer_list_size,
    _sample_buffer_to_mono_float32,
)


class _Block:
    def __init__(self, payload: bytes):
        self.payload = payload


class _Sample:
    def __init__(
        self,
        asbd: dict,
        block: _Block | None = None,
        audio_buffers: list[tuple[bytes, int]] | None = None,
        valid: bool = True,
    ):
        self.asbd = asbd
        self.block = block
        self.audio_buffers = audio_buffers or []
        self.valid = valid


class _AudioBuffer:
    mData = None
    mDataByteSize = 0
    mNumberChannels = 1


class _AudioBufferList:
    def __init__(self, count: int):
        self._buffers = [_AudioBuffer() for _ in range(count)]

    def __len__(self):
        return len(self._buffers)

    def __getitem__(self, index: int):
        return self._buffers[index]


def _install_core_audio_fakes(monkeypatch):
    core_audio = types.ModuleType("CoreAudio")
    core_audio.kAudioFormatFlagIsFloat = 1
    core_audio.kAudioFormatFlagIsBigEndian = 2
    core_audio.kAudioFormatFlagIsSignedInteger = 4
    core_audio.kAudioFormatFlagIsNonInterleaved = 32
    core_audio.AudioBufferList = _AudioBufferList

    core_media = types.ModuleType("CoreMedia")
    core_media.kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment = 1
    core_media.CMSampleBufferIsValid = lambda sample: sample.valid
    core_media.CMSampleBufferGetFormatDescription = lambda sample: sample.asbd
    core_media.CMAudioFormatDescriptionGetStreamBasicDescription = lambda desc: desc
    core_media.CMSampleBufferGetDataBuffer = lambda sample: sample.block
    core_media.CMBlockBufferGetDataLength = lambda block: len(block.payload)

    def _copy_block(block, offset, length, destination):
        destination[:length] = block.payload[offset:offset + length]
        return 0

    core_media.CMBlockBufferCopyDataBytes = _copy_block

    def _copy_audio_buffers(sample, _size_out, buffers, _size, *_args):
        needed_size = _audio_buffer_list_size(max(1, len(sample.audio_buffers)))
        if buffers is None:
            return 0, needed_size, object()
        for index, (payload, channels) in enumerate(sample.audio_buffers):
            if index >= len(buffers):
                break
            buffers[index].mData = payload
            buffers[index].mDataByteSize = len(payload)
            buffers[index].mNumberChannels = channels
        return 0, needed_size, object()

    core_media.CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer = _copy_audio_buffers

    monkeypatch.setitem(sys.modules, "CoreAudio", core_audio)
    monkeypatch.setitem(sys.modules, "CoreMedia", core_media)
    return core_audio


def test_sample_buffer_decodes_interleaved_float32(monkeypatch):
    core_audio = _install_core_audio_fakes(monkeypatch)
    frames = np.array([1.0, -1.0, 0.5, 0.25], dtype="<f4")
    sample = _Sample(
        asbd={
            "mChannelsPerFrame": 2,
            "mBitsPerChannel": 32,
            "mFormatFlags": core_audio.kAudioFormatFlagIsFloat,
            "mSampleRate": 44100,
            "mBytesPerFrame": 8,
        },
        block=_Block(frames.tobytes()),
    )

    arr = _sample_buffer_to_mono_float32(sample)

    assert np.allclose(arr, np.array([0.0, 0.375], dtype=np.float32))
    assert arr._voiceflow_sample_rate == 44100


def test_sample_buffer_decodes_non_interleaved_audio_buffers(monkeypatch):
    core_audio = _install_core_audio_fakes(monkeypatch)
    left = np.array([1.0, 0.0], dtype="<f4")
    right = np.array([-1.0, 0.5], dtype="<f4")
    sample = _Sample(
        asbd={
            "mChannelsPerFrame": 2,
            "mBitsPerChannel": 32,
            "mFormatFlags": (
                core_audio.kAudioFormatFlagIsFloat
                | core_audio.kAudioFormatFlagIsNonInterleaved
            ),
            "mSampleRate": 48000,
            "mBytesPerFrame": 4,
        },
        audio_buffers=[(left.tobytes(), 1), (right.tobytes(), 1)],
    )

    arr = _sample_buffer_to_mono_float32(sample)

    assert np.allclose(arr, np.array([0.0, 0.25], dtype=np.float32))
    assert arr._voiceflow_sample_rate == 48000


def test_sample_buffer_returns_empty_for_invalid_buffer(monkeypatch):
    _install_core_audio_fakes(monkeypatch)
    sample = _Sample(asbd={}, valid=False)

    arr = _sample_buffer_to_mono_float32(sample)

    assert arr.size == 0
