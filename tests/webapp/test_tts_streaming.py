"""Tests for PCM rechunking helpers."""

from webapp.tts.streaming import iter_rechunked_pcm


def test_iter_rechunked_pcm_splits_and_flushes_remainder():
  data = [b"\x00\x01" * 3, b"\x02\x03" * 2]
  chunks = list(iter_rechunked_pcm(iter(data), 24000, chunk_samples=4))
  assert chunks
  assert all(rate == 24000 for rate, _pcm in chunks)
  assert b"".join(pcm for _rate, pcm in chunks) == b"\x00\x01" * 3 + b"\x02\x03" * 2
