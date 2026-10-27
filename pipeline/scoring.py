"""线索打分的 band 判定（band 由代码计算，不信任模型自报）。"""
from __future__ import annotations

BAND_CONTACT_NOW = "contact-now"
BAND_MAYBE = "maybe"
BAND_SKIP = "skip"


def band_for(total: int, max_total: int) -> str:
    """总分占比 >=70% → contact-now；>=40% → maybe；否则 skip。"""
    if max_total <= 0:
        return BAND_SKIP
    ratio = total / max_total
    if ratio >= 0.7:
        return BAND_CONTACT_NOW
    if ratio >= 0.4:
        return BAND_MAYBE
    return BAND_SKIP
