"""F152 隐私采集的纯状态转换。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn

from octoagent.core.models.privacy_ingestion import (
    AnalysisResult,
    ApprovedAnalysisPacket,
    ConsentGrant,
    MemoryReviewState,
    OptionalMemoryCandidate,
    ReviewBundle,
    canonical_sha256,
)


class PrivacyIngestionError(ValueError):
    """带稳定错误码的隐私采集合同拒绝。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _reject(code: str, detail: str = "") -> NoReturn:
    raise PrivacyIngestionError(code, detail)


@dataclass(frozen=True, slots=True)
class ConsentConsumption:
    """一次 consent 消费必须精确绑定的 scope 与时间。"""

    bundle_sha256: str
    approved_packet_sha256: str
    purpose: str
    owner_id: str
    device_id: str
    used_at: datetime


def consume_consent(
    grant: ConsentGrant,
    consumption: ConsentConsumption,
) -> ConsentGrant:
    """验证精确 scope 后原子语义地生成已消费 consent。"""

    if grant.used_at is not None:
        _reject("CONSENT_ALREADY_USED")
    expected_scope = (
        grant.bundle_sha256,
        grant.approved_packet_sha256,
        grant.purpose,
        grant.owner_id,
        grant.device_id,
    )
    actual_scope = (
        consumption.bundle_sha256,
        consumption.approved_packet_sha256,
        consumption.purpose,
        consumption.owner_id,
        consumption.device_id,
    )
    if actual_scope != expected_scope:
        _reject("CONSENT_SCOPE_MISMATCH")
    if consumption.used_at < grant.approved_at:
        _reject("CONSENT_TIME_INVALID")
    if consumption.used_at >= grant.expires_at:
        _reject("CONSENT_EXPIRED")
    return ConsentGrant.model_validate(
        grant.model_dump(mode="python") | {"used_at": consumption.used_at}
    )


@dataclass(frozen=True, slots=True)
class ApprovedPacketTransition:
    packet: ApprovedAnalysisPacket
    consumed_consent: ConsentGrant


def approve_review_bundle(
    bundle: ReviewBundle,
    packet: ApprovedAnalysisPacket,
    consent: ConsentGrant,
    *,
    used_at: datetime,
) -> ApprovedPacketTransition:
    """验证同一预览、同一 provenance 与同一 packet 后消费 consent。"""

    if used_at >= bundle.retention.expires_at:
        _reject("REVIEW_BUNDLE_EXPIRED")
    if canonical_sha256(bundle) != consent.bundle_sha256:
        _reject("REVIEW_BUNDLE_MISMATCH")
    if canonical_sha256(packet) != consent.approved_packet_sha256:
        _reject("APPROVED_PACKET_MISMATCH")
    if (
        packet.consent_id != consent.consent_id
        or packet.purpose != consent.purpose
        or packet.retention.expires_at > consent.expires_at
    ):
        _reject("CONSENT_SCOPE_MISMATCH")
    if packet.provenance != bundle.provenance:
        _reject("PROVENANCE_MISMATCH")
    if any(
        provenance.owner_id != consent.owner_id or provenance.device_id != consent.device_id
        for provenance in packet.provenance
    ):
        _reject("CONSENT_SCOPE_MISMATCH")
    consumed = consume_consent(
        consent,
        ConsentConsumption(
            bundle_sha256=canonical_sha256(bundle),
            approved_packet_sha256=canonical_sha256(packet),
            purpose=packet.purpose,
            owner_id=consent.owner_id,
            device_id=consent.device_id,
            used_at=used_at,
        ),
    )
    return ApprovedPacketTransition(packet=packet, consumed_consent=consumed)


def accept_analysis_result(
    packet: ApprovedAnalysisPacket,
    result: AnalysisResult,
) -> AnalysisResult:
    """接受同一 packet 的结果，但绝不隐式生成 Memory candidate。"""

    if result.packet_id != packet.packet_id:
        _reject("ANALYSIS_RESULT_LINEAGE_MISMATCH")
    if result.created_at < packet.retention.created_at:
        _reject("ANALYSIS_RESULT_TIME_INVALID")
    return result


def propose_memory_candidate(
    packet: ApprovedAnalysisPacket,
    result: AnalysisResult,
    candidate: OptionalMemoryCandidate,
) -> OptionalMemoryCandidate:
    """只接受用户显式选择且仍待既有 Memory review 的候选。"""

    accept_analysis_result(packet, result)
    if candidate.review_state is not MemoryReviewState.PENDING:
        _reject("MEMORY_REVIEW_STATE_INVALID")
    if candidate.result_id != result.result_id:
        _reject("MEMORY_RESULT_LINEAGE_MISMATCH")
    if candidate.packet_sha256 != canonical_sha256(packet):
        _reject("MEMORY_PACKET_LINEAGE_MISMATCH")
    if candidate.provenance != packet.provenance:
        _reject("PROVENANCE_MISMATCH")
    return candidate


__all__ = [
    "ApprovedPacketTransition",
    "ConsentConsumption",
    "PrivacyIngestionError",
    "accept_analysis_result",
    "approve_review_bundle",
    "consume_consent",
    "propose_memory_candidate",
]
