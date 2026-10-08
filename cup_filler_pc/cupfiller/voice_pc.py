"""Nhận dạng mức nước bằng GIỌNG NÓI trên PC (nhận PCM do ESP32 gửi lên qua UART).

Có 2 đường, tự chọn theo thứ có sẵn (``cupfiller.config: voice.engine``):

  1. ``vosk``  - ASR tiếng Việt offline (cài ``pip install vosk`` + tải model
     ``vosk-model-small-vn``): đọc ra chữ, rồi bộ phân tích số tiếng Việt bên dưới
     đổi "một trăm năm mươi mililít" -> 150 ml.
  2. ``peaks`` - không cần cài gì: đếm số TIẾNG trong đoạn nói (mỗi tiếng là một
     đỉnh năng lượng). Người dùng nói "một" (1 tiếng) = preset 100 ml, "hai" = 150,
     "ba" = 200... hoặc vỗ tay N lần. Cách này "ngây thơ" nhưng chạy ngay, không
     phụ thuộc mạng, và rất hợp với mic rẻ + ESP32.

Bộ phân tích tiếng Việt cũng nhận câu lệnh ngắn: "rót"/"bơm" = bắt đầu, "dừng"/"tắt" = dừng.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "VietnameseAmount",
    "parse_vietnamese_amount",
    "parse_voice_command",
    "PeakCounter",
    "PeakResult",
    "VoiceRecognizer",
    "VoiceResult",
    "to_ascii",
]

# ---------------------------------------------------------------------------
#  Chuẩn hoá văn bản tiếng Việt (bỏ dấu) để so từ khoá cho chắc
# ---------------------------------------------------------------------------
def to_ascii(text: str) -> str:
    """'một trăm' -> 'mot tram' (bỏ dấu, viết thường, gộp khoảng trắng)."""
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", text.lower()).strip()


# ---------------------------------------------------------------------------
#  Số tiếng Việt -> int
# ---------------------------------------------------------------------------
UNITS = {
    "khong": 0, "mot": 1, "moc": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4,
    "nam": 5, "lam": 5, "sau": 6, "bay": 7, "tam": 8, "chin": 9,
}
MULTIPLIERS = {"muoi": 10, "chuc": 10, "tram": 100, "nghin": 1000, "ngan": 1000,
               "trieu": 1_000_000}
FILLERS = {"linh", "le", "le", "va", "ruoi", "may", "am", "cai", "muc", "nuoc",
           "rot", "bom", "do", "day", "con", "cho", "toi", "mot_cai"}


def words_to_number(words: Sequence[str]) -> Optional[float]:
    """Đổi dãy từ số tiếng Việt thành số (hỗ trợ tới hàng triệu).

        ['mot','tram','nam','muoi']  -> 150      ['hai','tram']       -> 200
        ['muoi','lam']               -> 15       ['mot','tram','ruoi'] -> 150
        ['ba']                       -> 3        ['hai','nghin']      -> 2000
    """
    total = 0.0        # phần đã chốt (hàng nghìn / triệu)
    group = 0.0        # nhóm đang xây (0..999)
    pending: Optional[float] = None      # chữ số đứng trước "mươi/trăm/nghìn"
    got = False
    half = False
    for raw in words:
        w = raw.strip()
        if not w or w in ("linh", "le", "va", "am", "cai", "muc", "nuoc", "may", "cai_gi"):
            continue
        if w == "ruoi":
            half = True
            got = True
            continue
        if w.isdigit():
            pending = float(w)
            got = True
            continue
        if w in UNITS:
            pending = float(UNITS[w])
            got = True
            continue
        if w in ("muoi", "chuc"):
            group += (pending if pending is not None else 1.0) * 10.0
            pending = None
            got = True
            continue
        if w == "tram":
            group += (pending if pending is not None else 1.0) * 100.0
            pending = None
            got = True
            continue
        if w in ("nghin", "ngan", "trieu"):
            if pending is not None:
                group += pending
                pending = None
            scale = 1_000_000.0 if w == "trieu" else 1000.0
            total += (group if group else 1.0) * scale
            group = 0.0
            got = True
            continue
        # từ lạ: bỏ qua
    if pending is not None:
        group += pending
    value = total + group
    if half:
        value += 50.0
    return value if got else None


def parse_vietnamese_amount(text: str) -> Optional[float]:
    """Đọc số ml từ câu tiếng Việt. Trả về None nếu không thấy số.

    Ví dụ: 'một trăm năm mươi mililit' -> 150 ; 'hai trăm ml' -> 200 ;
           'ba trăm' -> 300 ; '250' -> 250 ; 'một lít' -> 1000.
    """
    plain = to_ascii(text)
    if not plain:
        return None
    # số viết bằng chữ số
    digits = re.findall(r"\d+(?:[.,]\d+)?", plain)
    words = [w for w in re.split(r"[\s,.;!?]+", plain) if w]
    value = words_to_number(words)
    if value is None and digits:
        value = float(digits[0].replace(",", "."))
    if value is None:
        return None
    # 'lit' -> ml (chỉ khi có chữ lít)
    if re.search(r"\blit\b|\bl\b", plain) and value <= 10:
        value *= 1000.0
    return float(value)


# ---------------------------------------------------------------------------
@dataclass
class VietnameseAmount:
    """Kết quả phân tích một câu nói về mức nước."""

    ml: Optional[float] = None
    index: Optional[int] = None
    text: str = ""
    start: bool = False        # câu có ý "rót"/"bơm"
    stop: bool = False         # câu có ý "dừng"/"tắt"
    matched: bool = False

    def as_dict(self) -> Dict:
        return {"ml": self.ml, "index": self.index, "text": self.text,
                "start": self.start, "stop": self.stop, "matched": self.matched}


START_WORDS = ("rot", "bom", "bat dau", "start", "lay", "day nuoc", "do nuoc")
STOP_WORDS = ("dung", "tat", "stop", "thoi", "huy")


def parse_voice_command(text: str, presets: Sequence[int] = (100, 150, 200, 250, 300),
                        tolerance: float = 0.18) -> VietnameseAmount:
    """Câu nói -> (mức ml, chỉ số preset, có ý rót/dừng hay không)."""
    plain = to_ascii(text)
    res = VietnameseAmount(text=text or "")
    words = [w for w in re.split(r"[\s,.;!?]+", plain) if w]
    res.start = any(k in plain for k in START_WORDS)
    res.stop = any(k in plain for k in STOP_WORDS)
    ml = parse_vietnamese_amount(text or "")
    if ml is None:
        # "mức ba" -> số thứ tự preset (ba -> preset thứ 3 = index 2)
        for i, w in enumerate(words):
            if w in UNITS and i > 0 and words[i - 1] in ("muc", "preset", "so", "muc_so"):
                res.index = max(0, min(UNITS[w] - 1, len(presets) - 1))
                res.ml = float(list(presets)[res.index])
                res.matched = True
                return res
        return res
    res.ml = ml
    presets = list(presets)
    best_i, best_d = None, None
    for i, p in enumerate(presets):
        d = abs(float(p) - ml)
        if best_d is None or d < best_d:
            best_i, best_d = i, d
    if best_i is not None and best_d is not None and best_d <= tolerance * max(float(presets[best_i]), 1.0):
        res.index = best_i
        res.ml = float(presets[best_i])
        res.matched = True
    elif best_i is not None:
        # ngoài dải preset: vẫn nhận nếu nằm giữa dải, cắt về preset gần nhất
        lo, hi = min(presets), max(presets)
        if lo * 0.7 <= ml <= hi * 1.3:
            res.index = best_i
            res.ml = float(presets[best_i])
            res.matched = True
    return res


# ---------------------------------------------------------------------------
#  Đếm "tiếng" trong đoạn âm thanh (không cần ASR)
# ---------------------------------------------------------------------------
@dataclass
class PeakResult:
    n_peaks: int = 0
    rms: float = 0.0
    dur_ms: int = 0
    confidence: int = 0
    envelope: Optional[np.ndarray] = None

    def as_dict(self) -> Dict:
        return {"n_peaks": self.n_peaks, "rms": self.rms, "dur_ms": self.dur_ms,
                "confidence": self.confidence}


class PeakCounter:
    """Đếm số "tiếng" (âm tiết) trong PCM 16-bit mono.

    Thuật toán: bình phương -> lọc trung bình trượt (bao năng lượng) -> ngưỡng thích
    ứng (giữa mức nền và mức đỉnh) -> đếm số lần bao vượt ngưỡng, có trễ (hysteresis)
    và khoảng cách tối thiểu để một tiếng không bị đếm thành hai.
    """

    def __init__(self, sample_rate: int = 16000, min_gap_ms: int = 120,
                 min_burst_ms: int = 60, frame_ms: float = 10.0):
        self.sample_rate = int(sample_rate)
        self.min_gap = int(min_gap_ms * self.sample_rate / 1000)
        self.min_burst = max(1, int(min_burst_ms * self.sample_rate / 1000))
        self.frame = max(1, int(frame_ms * self.sample_rate / 1000))

    def analyze(self, samples: np.ndarray | Sequence[int]) -> PeakResult:
        x = np.asarray(samples, dtype=np.float32)
        n = x.size
        if n == 0:
            return PeakResult()
        rms = float(np.sqrt(np.mean(x * x)))
        # bao năng lượng theo khung 10 ms
        nfr = max(1, n // self.frame)
        env = np.zeros(nfr, dtype=np.float32)
        for i in range(nfr):
            seg = x[i * self.frame : (i + 1) * self.frame]
            env[i] = np.sqrt(float(np.mean(seg * seg)) + 1e-9)
        k = max(1, int(0.02 * self.sample_rate / self.frame))     # làm mượt 20 ms
        env = np.convolve(env, np.ones(k, dtype=np.float32) / k, mode="same")
        floor = float(np.percentile(env, 20))
        top = float(np.percentile(env, 95))
        if top <= 3.0 * max(floor, 1e-6) or top < 150.0:          # không có tiếng nào
            return PeakResult(n_peaks=0, rms=rms, dur_ms=int(1000 * n / self.sample_rate),
                              envelope=env)
        thr_on = floor + 0.45 * (top - floor)
        thr_off = floor + 0.25 * (top - floor)
        frame_ms = 1000.0 * self.frame / self.sample_rate
        min_gap_fr = max(1, int(self.min_gap / self.frame))
        min_burst_fr = max(1, int(self.min_burst / self.frame))
        peaks, run, last_end = 0, 0, -10 ** 9
        for i, v in enumerate(env):
            if v >= thr_on:
                run += 1
            else:
                if v < thr_off and run > 0:
                    if run >= min_burst_fr and (i - run - last_end) >= min_gap_fr:
                        peaks += 1
                        last_end = i
                    run = 0
        if run >= min_burst_fr:
            peaks += 1
        dur_ms = int(1000.0 * n / self.sample_rate)
        # độ tin cậy: ưu tiên đoạn có tiếng rõ, không quá ngắn/quá dài
        conf = int(max(0, min(100, 100 * (top - floor) / max(top, 1e-6))))
        if dur_ms < 150:
            conf = int(conf * 0.5)
        return PeakResult(n_peaks=peaks, rms=rms, dur_ms=dur_ms, confidence=conf, envelope=env)


# ---------------------------------------------------------------------------
@dataclass
class VoiceResult:
    """Kết quả cuối cùng cho một đoạn nói."""

    ok: bool = False
    index: Optional[int] = None
    ml: Optional[float] = None
    text: str = ""
    engine: str = "none"
    confidence: int = 0
    n_peaks: int = 0
    stop: bool = False
    reason: str = ""

    def as_dict(self) -> Dict:
        return {"ok": self.ok, "index": self.index, "ml": self.ml, "text": self.text,
                "engine": self.engine, "confidence": self.confidence,
                "n_peaks": self.n_peaks, "stop": self.stop, "reason": self.reason}


class VoiceRecognizer:
    """Ghép các chunk PCM từ ESP32 thành đoạn nói rồi "hiểu" thành mức nước.

    Cách dùng::

        vr = VoiceRecognizer(presets=(100,150,200,250,300), engine="auto")
        for chunk in stream:                 # chunk: dict của parse_audio_chunk
            vr.feed(chunk)
            res = vr.result_if_ready()       # None nếu đoạn nói chưa kết thúc
    """

    def __init__(self, presets: Sequence[int] = (100, 150, 200, 250, 300),
                 sample_rate: int = 16000, engine: str = "auto",
                 vosk_model_path: str = "", log=None):
        self.presets = list(presets)
        self.sample_rate = int(sample_rate)
        self.engine = self._pick_engine(engine, vosk_model_path)
        self.vosk_model_path = vosk_model_path
        self.log = log or (lambda msg: None)
        self.peak = PeakCounter(sample_rate=self.sample_rate)
        self._buf: List[int] = []
        self._active = False
        self.segment_ms = 0
        self._vosk = None
        self._rec = None

    # ---- chọn engine --------------------------------------------------
    def _pick_engine(self, engine: str, model_path: str) -> str:
        engine = (engine or "auto").lower()
        if engine in ("vosk", "auto"):
            if self._load_vosk(model_path):
                return "vosk"
            if engine == "vosk":
                self.log("[voice] không nạp được vosk -> dùng cách đếm tiếng")
        return "peaks"

    def _load_vosk(self, model_path: str) -> bool:
        if not model_path or not os.path.isdir(model_path):
            return False
        try:
            from vosk import Model, KaldiRecognizer  # type: ignore

            self._vosk = (Model, KaldiRecognizer)
            self._vosk_model = Model(model_path)
            self._rec = KaldiRecognizer(self._vosk_model, self.sample_rate)
            self.log("[voice] dùng Vosk ASR: %s" % model_path)
            return True
        except Exception as exc:                     # pragma: no cover - tuỳ máy
            self.log("[voice] Vosk lỗi (%s) -> đếm tiếng" % exc)
            return False

    # ---- nạp audio ----------------------------------------------------
    def feed(self, chunk: Dict) -> None:
        """Nạp một gói AUDIO_CHUNK đã giải mã (parse_audio_chunk)."""
        samples = chunk.get("samples") or []
        if chunk.get("start"):
            self._buf = []
            self._active = True
            self._start_acoustic_engine()
        if self._active or samples:
            self._buf.extend(int(s) for s in samples)
        if chunk.get("end"):
            self._active = False

    def _start_acoustic_engine(self) -> None:
        if self.engine == "vosk" and self._vosk is not None:
            Model, KaldiRecognizer = self._vosk
            try:
                self._rec = KaldiRecognizer(self._vosk_model, self.sample_rate)
            except Exception:
                self._rec = None

    def push_pcm(self, samples: Sequence[int], start: bool = False, end: bool = True) -> None:
        """Nạp PCM trực tiếp (tiện cho test / nguồn khác)."""
        if start:
            self.feed({"start": True, "end": False, "samples": list(samples)})
        else:
            self.feed({"start": False, "end": end, "samples": list(samples)})

    # ---- kết quả ------------------------------------------------------
    @property
    def has_segment(self) -> bool:
        return bool(self._buf) and not self._active

    def analyze(self, fallback_peaks: int = 0) -> VoiceResult:
        """Phân tích đoạn đã gom -> kết quả mức nước."""
        pcm = np.asarray(self._buf, dtype=np.int16) if self._buf else np.zeros(0, np.int16)
        self._buf = []
        if pcm.size == 0:
            if fallback_peaks:
                return self._result_from_peaks(fallback_peaks, 0.0,
                                               int(1000 * 0 / self.sample_rate), "esp")
            return VoiceResult(reason="không có âm thanh")
        if self.engine == "vosk" and self._rec is not None:
            text = ""
            try:
                if self._rec.AcceptWaveform(pcm.tobytes()):
                    import json as _json
                    text = _json.loads(self._rec.Result()).get("text", "")
                text = text or _json.loads(self._rec.FinalResult()).get("text", "")
            except Exception as exc:                 # pragma: no cover
                self.log("[voice] Vosk lỗi khi nhận dạng: %s" % exc)
            if text.strip():
                cmd = parse_voice_command(text, self.presets)
                res = VoiceResult(ok=cmd.matched, index=cmd.index, ml=cmd.ml, text=text,
                                  engine="vosk", confidence=90, stop=cmd.stop)
                if not res.ok:
                    res.reason = "nghe được '%s' nhưng không ra mức nước" % text
                return res
        pk = self.peak.analyze(pcm)
        return self._result_from_peaks(pk.n_peaks, pk.rms, pk.dur_ms, "peaks", pk.confidence)

    def _result_from_peaks(self, n_peaks: int, rms: float, dur_ms: int, engine: str,
                           confidence: int = 0) -> VoiceResult:
        res = VoiceResult(engine=engine, n_peaks=n_peaks, confidence=confidence)
        if n_peaks <= 0:
            res.reason = "không nghe thấy tiếng nào"
            return res
        idx = n_peaks - 1
        if idx >= len(self.presets):
            res.reason = "có %d tiếng, chỉ có %d preset" % (n_peaks, len(self.presets))
            return res
        res.ok = True
        res.index = idx
        res.ml = float(self.presets[idx])
        res.text = "%d tiếng -> mức %g ml" % (n_peaks, res.ml)
        if confidence:
            res.confidence = min(confidence, 100)
        return res

    def result_if_ready(self) -> Optional[VoiceResult]:
        if self.has_segment:
            return self.analyze()
        return None
