"""F154 健康预览提交的单一应用编排。"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

import aiosqlite
from octoagent.core.models import (
    AnalysisResult,
    ApprovedAnalysisPacket,
    AuditDecision,
    AuditResult,
    ConsentGrant,
    DeletionReceipt,
    DeletionStatus,
    DeviceCapability,
    PrivacyAuditEvent,
    PrivacyAuditEventType,
    RetentionState,
    ReviewBundle,
    canonical_json_bytes,
    canonical_sha256,
)
from octoagent.core.privacy_ingestion import (
    PrivacyIngestionError,
    accept_analysis_result,
    approve_review_bundle,
)
from octoagent.core.store import StoreGroup
from octoagent.core.store.device_trust_store import SqliteDeviceTrustStore
from octoagent.core.store.privacy_ingestion_store import SqlitePrivacyIngestionStore
from octoagent.policy.privacy_ingestion_policy import AuthorizedRequest
from octoagent.protocol.device_trust import DeviceProofHeaders
from octoagent.protocol.privacy_ingestion import (
    PrivacyConsumer,
    PrivacyContractName,
    validate_consumer_payload,
)
from octoagent.provider import ProviderRouter
from octoagent.provider.exceptions import ProviderError
from octoagent.provider.provider_client import LLMCallError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .mobile_device_auth import (
    DeviceAuthError,
    MobileAuthorizationRequest,
    authorize_mobile_request,
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _new_id() -> str:
    return str(uuid4())


def _identifier_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _health_auth_reason(reason_code: str) -> str:
    return {
        "CAPABILITY_DENIED": "HEALTH_CAPABILITY_DENIED",
        "REQUEST_REPLAYED": "HEALTH_REQUEST_REPLAYED",
    }.get(reason_code, reason_code)


def _health_validation_reason(exc: ValidationError | ValueError) -> str:
    if isinstance(exc, ValidationError) and any(
        error.get("type") == "extra_forbidden" for error in exc.errors()
    ):
        return "HEALTH_RAW_FIELD_FORBIDDEN"
    if "preview hash" in str(exc).casefold():
        return "HEALTH_PREVIEW_HASH_MISMATCH"
    return "HEALTH_REVIEW_INVALID"


@dataclass(frozen=True, slots=True)
class HealthIngestionServiceOptions:
    """health review 编排的可注入时钟与标识生成器。"""

    clock: Callable[[], datetime] = _utc_now
    id_factory: Callable[[], str] = _new_id
    analysis_timeout_s: float = 60.0


@dataclass(frozen=True, slots=True)
class HealthRequestContext:
    headers: DeviceProofHeaders
    method: str
    canonical_path: str
    raw_body: bytes


@dataclass(frozen=True, slots=True)
class HealthAuditOutcome:
    event_type: PrivacyAuditEventType
    capability: DeviceCapability
    decision: AuditDecision
    result: AuditResult
    reason_code: str


class HealthReviewAccepted(BaseModel):
    """review durable 后返回给 iOS 的最小非敏感确认。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime


class HealthAnalysisTimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_window(self) -> HealthAnalysisTimeRange:
        if self.end <= self.start or self.end - self.start > timedelta(days=7):
            raise ValueError("health analysis time range is invalid")
        return self


class HealthDailyStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    local_day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    count: str = Field(pattern=r"^(0|[1-9]\d*)(\.\d+)?$")
    unit: Literal["count"]


class HealthSleepStages(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    awake: str = Field(pattern=r"^(0|[1-9]\d*)(\.\d+)?$")
    core: str = Field(pattern=r"^(0|[1-9]\d*)(\.\d+)?$")
    deep: str = Field(pattern=r"^(0|[1-9]\d*)(\.\d+)?$")
    rem: str = Field(pattern=r"^(0|[1-9]\d*)(\.\d+)?$")
    unspecified: str = Field(pattern=r"^(0|[1-9]\d*)(\.\d+)?$")


class HealthSleepFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_start_utc: datetime
    window_end_utc: datetime
    total_asleep_minutes: str = Field(pattern=r"^(0|[1-9]\d*)(\.\d+)?$")
    stage_minutes: HealthSleepStages
    unit: Literal["min"]


class HealthApprovedFacts(BaseModel):
    """仅存在于单次 provider 调用栈的 approved aggregate facts。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: Literal["summarize_recent_activity_and_sleep"]
    time_range: HealthAnalysisTimeRange
    daily_steps: tuple[HealthDailyStep, ...] = ()
    sleep: HealthSleepFacts | None = None
    completeness_notice: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_facts(self) -> HealthApprovedFacts:
        if not self.daily_steps and self.sleep is None:
            raise ValueError("health analysis facts must not be empty")
        return self


class HealthAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    consent: dict[str, Any]
    packet: dict[str, Any]
    approved_facts: HealthApprovedFacts


class HealthAnalysisAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: AnalysisResult
    summary: str = Field(min_length=1)


class HealthIngestionError(ValueError):
    """只向 route 暴露稳定 reason code，不携带健康事实。"""

    def __init__(self, reason_code: str, *, status_code: int) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class HealthIngestionService:
    """复用 F153 device proof 与 F152 store/audit 的唯一 health service。"""

    def __init__(
        self,
        *,
        device_store: SqliteDeviceTrustStore,
        privacy_store: SqlitePrivacyIngestionStore,
        options: HealthIngestionServiceOptions,
    ) -> None:
        self._device_store = device_store
        self._privacy_store = privacy_store
        self._clock = options.clock
        self._id_factory = options.id_factory
        self._analysis_timeout_s = options.analysis_timeout_s

    async def submit_review(
        self,
        *,
        headers: DeviceProofHeaders,
        method: str,
        canonical_path: str,
        raw_body: bytes,
        payload: Mapping[str, Any],
    ) -> HealthReviewAccepted:
        """验设备证明后验证并持久化一个 exact F154 ReviewBundle。"""

        now = self._clock()
        authorized = await self._authorize(
            context=HealthRequestContext(
                headers=headers,
                method=method,
                canonical_path=canonical_path,
                raw_body=raw_body,
            ),
            now=now,
            required_capability=DeviceCapability.HEALTH_REVIEW_SUBMIT,
        )
        review = self._validate_review(payload)
        owner_id = await self._require_review_scope(
            device_id=authorized.device_id,
            review=review,
        )
        bundle_sha256 = await self._persist_review(
            review=review,
            owner_id=owner_id,
            device_id=authorized.device_id,
            now=now,
        )
        return HealthReviewAccepted(
            bundle_sha256=bundle_sha256,
            expires_at=review.retention.expires_at,
        )

    async def submit_analysis(
        self,
        *,
        context: HealthRequestContext,
        request: HealthAnalysisRequest,
        provider_router: ProviderRouter,
    ) -> HealthAnalysisAccepted:
        """消费一次 approved packet 并直连 ProviderRouter；失败绝不 Echo。"""

        now = self._clock()
        authorized = await self._authorize(
            context=context,
            now=now,
            required_capability=DeviceCapability.HEALTH_ANALYSIS_RUN,
        )
        packet, packet_hash, review = await self._approve_analysis_packet(
            request=request,
            authorized=authorized,
            now=now,
        )
        try:
            summary = await self._call_analysis_model(
                provider_router=provider_router,
                packet=packet,
                facts=request.approved_facts,
            )
        except (ProviderError, LLMCallError, TimeoutError) as exc:
            await self._audit_analysis_failure(
                packet_hash=packet_hash,
                review=review,
                reason_code="HEALTH_ANALYSIS_FAILED",
                now=now,
            )
            raise HealthIngestionError("HEALTH_ANALYSIS_FAILED", status_code=502) from exc
        result = await self._persist_analysis_result(
            packet=packet,
            packet_hash=packet_hash,
            summary=summary,
            review=review,
            now=now,
        )
        return HealthAnalysisAccepted(result=result, summary=summary)

    async def delete_source(
        self,
        *,
        context: HealthRequestContext,
        source_hash: str,
    ) -> DeletionReceipt:
        """删除同一 provenance 全链；失败 receipt 可用同一 request id 重入。"""

        now = self._clock()
        authorized = await self._authorize(
            context=context,
            now=now,
            required_capability=DeviceCapability.HEALTH_SOURCE_DELETE,
        )
        request_id = self._deletion_request_id(authorized.device_id, source_hash)
        existing = await self._privacy_store.get_deletion_receipt(request_id)
        if existing is not None and existing.status is DeletionStatus.COMPLETED:
            return existing
        review = await self._load_scoped_review(
            source_hash=source_hash,
            device_id=authorized.device_id,
        )
        await self._append_health_audit(
            review=review,
            object_hash=source_hash,
            outcome=HealthAuditOutcome(
                event_type=PrivacyAuditEventType.DELETION_STARTED,
                capability=DeviceCapability.HEALTH_SOURCE_DELETE,
                decision=AuditDecision.ALLOW,
                result=AuditResult.PENDING,
                reason_code="HEALTH_DELETION_STARTED",
            ),
            now=now,
        )
        receipt = await self._privacy_store.delete_source_chain(
            request_id=request_id,
            source_hash=source_hash,
            started_at=now,
        )
        if receipt.status is DeletionStatus.FAILED:
            await self._append_health_audit(
                review=review,
                object_hash=source_hash,
                outcome=HealthAuditOutcome(
                    event_type=PrivacyAuditEventType.DELETION_FAILED,
                    capability=DeviceCapability.HEALTH_SOURCE_DELETE,
                    decision=AuditDecision.ALLOW,
                    result=AuditResult.FAILURE,
                    reason_code="HEALTH_DELETION_INCOMPLETE",
                ),
                now=receipt.finished_at or now,
            )
            raise HealthIngestionError("HEALTH_DELETION_INCOMPLETE", status_code=503)
        await self._append_health_audit(
            review=review,
            object_hash=source_hash,
            outcome=HealthAuditOutcome(
                event_type=PrivacyAuditEventType.DELETION_COMPLETED,
                capability=DeviceCapability.HEALTH_SOURCE_DELETE,
                decision=AuditDecision.ALLOW,
                result=AuditResult.SUCCESS,
                reason_code="HEALTH_DELETION_COMPLETED",
            ),
            now=receipt.finished_at or now,
        )
        return receipt

    async def _authorize(
        self,
        *,
        context: HealthRequestContext,
        now: datetime,
        required_capability: DeviceCapability,
    ) -> AuthorizedRequest:
        try:
            return await authorize_mobile_request(
                self._device_store,
                MobileAuthorizationRequest(
                    headers=context.headers,
                    method=context.method,
                    canonical_path=context.canonical_path,
                    raw_body=context.raw_body,
                    required_capability=required_capability.value,
                    now=now,
                ),
            )
        except DeviceAuthError as exc:
            raise HealthIngestionError(
                _health_auth_reason(exc.reason_code),
                status_code=401,
            ) from exc

    @staticmethod
    def _validate_review(payload: Mapping[str, Any]) -> ReviewBundle:
        try:
            validated = validate_consumer_payload(
                PrivacyConsumer.F154,
                PrivacyContractName.REVIEW_BUNDLE,
                payload,
            )
        except (ValidationError, ValueError) as exc:
            raise HealthIngestionError(
                _health_validation_reason(exc),
                status_code=422,
            ) from exc
        if not isinstance(validated, ReviewBundle):
            raise HealthIngestionError("HEALTH_REVIEW_INVALID", status_code=422)
        return validated

    async def _require_review_scope(
        self,
        *,
        device_id: str,
        review: ReviewBundle,
    ) -> str:
        provenance = review.provenance[0]
        device = await self._device_store.get_device(device_id)
        if device is None:
            raise HealthIngestionError("DEVICE_NOT_FOUND", status_code=401)
        if provenance.owner_id != device.owner_id or provenance.device_id != device_id:
            raise HealthIngestionError("HEALTH_REVIEW_SCOPE_MISMATCH", status_code=403)
        return device.owner_id

    async def _approve_analysis_packet(
        self,
        *,
        request: HealthAnalysisRequest,
        authorized: AuthorizedRequest,
        now: datetime,
    ) -> tuple[ApprovedAnalysisPacket, str, ReviewBundle]:
        review, consent, packet = await self._load_analysis_contracts(request)
        await self._validate_analysis_scope(
            review=review,
            packet=packet,
            consent=consent,
            device_id=authorized.device_id,
        )
        if canonical_sha256(request.approved_facts) != packet.facts_sha256:
            raise HealthIngestionError("HEALTH_PREVIEW_HASH_MISMATCH", status_code=422)
        try:
            approve_review_bundle(review, packet, consent, used_at=now)
            packet_hash = await self._privacy_store.put_approved_packet(
                request.source_hash,
                packet,
            )
        except aiosqlite.IntegrityError as exc:
            raise HealthIngestionError("HEALTH_CONSENT_INVALID", status_code=409) from exc
        except (PrivacyIngestionError, ValueError) as exc:
            raise HealthIngestionError("HEALTH_CONSENT_INVALID", status_code=422) from exc
        return packet, packet_hash, review

    async def _load_analysis_contracts(
        self,
        request: HealthAnalysisRequest,
    ) -> tuple[ReviewBundle, ConsentGrant, ApprovedAnalysisPacket]:
        try:
            stored_review = await self._privacy_store.get_review_bundle(request.source_hash)
            review = validate_consumer_payload(
                PrivacyConsumer.F154,
                PrivacyContractName.REVIEW_BUNDLE,
                stored_review.model_dump(mode="python"),
            )
            consent = validate_consumer_payload(
                PrivacyConsumer.F154,
                PrivacyContractName.CONSENT_GRANT,
                request.consent,
            )
            packet = validate_consumer_payload(
                PrivacyConsumer.F154,
                PrivacyContractName.APPROVED_ANALYSIS_PACKET,
                request.packet,
            )
        except (ValidationError, ValueError) as exc:
            raise HealthIngestionError("HEALTH_CONSENT_INVALID", status_code=422) from exc
        if (
            not isinstance(review, ReviewBundle)
            or not isinstance(consent, ConsentGrant)
            or not isinstance(packet, ApprovedAnalysisPacket)
        ):
            raise HealthIngestionError("HEALTH_CONSENT_INVALID", status_code=422)
        return review, consent, packet

    async def _validate_analysis_scope(
        self,
        *,
        review: ReviewBundle,
        packet: ApprovedAnalysisPacket,
        consent: ConsentGrant,
        device_id: str,
    ) -> None:
        device = await self._device_store.get_device(device_id)
        provenance = review.provenance[0]
        expected = (provenance.owner_id, provenance.device_id, review.purpose)
        actual = (consent.owner_id, consent.device_id, packet.purpose)
        if device is None or actual != expected:
            raise HealthIngestionError("HEALTH_CONSENT_INVALID", status_code=403)
        if device.owner_id != consent.owner_id or device.device_id != consent.device_id:
            raise HealthIngestionError("HEALTH_CONSENT_INVALID", status_code=403)

    async def _load_scoped_review(
        self,
        *,
        source_hash: str,
        device_id: str,
    ) -> ReviewBundle:
        try:
            stored = await self._privacy_store.get_review_bundle(source_hash)
            review = validate_consumer_payload(
                PrivacyConsumer.F154,
                PrivacyContractName.REVIEW_BUNDLE,
                stored.model_dump(mode="python"),
            )
        except (ValidationError, ValueError) as exc:
            raise HealthIngestionError("HEALTH_DELETION_INCOMPLETE", status_code=404) from exc
        if not isinstance(review, ReviewBundle):
            raise HealthIngestionError("HEALTH_DELETION_INCOMPLETE", status_code=404)
        await self._require_review_scope(device_id=device_id, review=review)
        return review

    @staticmethod
    def _deletion_request_id(device_id: str, source_hash: str) -> str:
        digest = hashlib.sha256(f"{device_id}:{source_hash}".encode()).hexdigest()
        return f"health-delete-{digest}"

    async def _call_analysis_model(
        self,
        *,
        provider_router: ProviderRouter,
        packet: ApprovedAnalysisPacket,
        facts: HealthApprovedFacts,
    ) -> str:
        resolved = provider_router.resolve_for_alias(
            "main",
            task_scope=f"health-analysis:{packet.packet_id}",
        )
        prompt = canonical_json_bytes(
            {
                "purpose": packet.purpose,
                "approved_facts": facts,
            }
        ).decode()
        content, tool_calls, _metadata = await asyncio.wait_for(
            resolved.client.call(
                instructions=(
                    "请只根据已批准的聚合健康事实生成普通语言概览和非医疗自我观察提示；"
                    "不得诊断、治疗或推断未提供的数据。"
                ),
                history=[{"role": "user", "content": prompt}],
                tools=[],
                model_name=resolved.model_name,
                reasoning=None,
            ),
            timeout=self._analysis_timeout_s,
        )
        summary = content.strip()
        if not summary or tool_calls:
            raise LLMCallError(
                "api_error",
                "health analysis returned no final text",
                retriable=False,
            )
        return summary

    async def _persist_analysis_result(
        self,
        *,
        packet: ApprovedAnalysisPacket,
        packet_hash: str,
        summary: str,
        review: ReviewBundle,
        now: datetime,
    ) -> AnalysisResult:
        result = accept_analysis_result(
            packet,
            AnalysisResult(
                result_id=self._id_factory(),
                packet_id=packet.packet_id,
                result_sha256=hashlib.sha256(summary.encode()).hexdigest(),
                created_at=now,
                retention_state=RetentionState.RETAINED,
            ),
        )
        result_hash = await self._privacy_store.put_analysis_result(packet_hash, result)
        await self._append_health_audit(
            review=review,
            object_hash=result_hash,
            outcome=HealthAuditOutcome(
                event_type=PrivacyAuditEventType.ANALYSIS_COMPLETED,
                capability=DeviceCapability.HEALTH_ANALYSIS_RUN,
                decision=AuditDecision.ALLOW,
                result=AuditResult.SUCCESS,
                reason_code="HEALTH_ANALYSIS_COMPLETED",
            ),
            now=now,
        )
        return result

    async def _audit_analysis_failure(
        self,
        *,
        packet_hash: str,
        review: ReviewBundle,
        reason_code: str,
        now: datetime,
    ) -> None:
        await self._append_health_audit(
            review=review,
            object_hash=packet_hash,
            outcome=HealthAuditOutcome(
                event_type=PrivacyAuditEventType.REJECTED,
                capability=DeviceCapability.HEALTH_ANALYSIS_RUN,
                decision=AuditDecision.DENY,
                result=AuditResult.FAILURE,
                reason_code=reason_code,
            ),
            now=now,
        )

    async def _append_health_audit(
        self,
        *,
        review: ReviewBundle,
        object_hash: str,
        outcome: HealthAuditOutcome,
        now: datetime,
    ) -> None:
        provenance = review.provenance[0]
        await self._privacy_store.append_audit(
            PrivacyAuditEvent(
                event_id=self._id_factory(),
                event_type=outcome.event_type,
                owner_hash=_identifier_hash(provenance.owner_id),
                device_hash=_identifier_hash(provenance.device_id),
                object_hash=object_hash,
                count=review.fact_count,
                data_types=provenance.data_types,
                capabilities=(outcome.capability,),
                decision=outcome.decision,
                result=outcome.result,
                reason_code=outcome.reason_code,
                occurred_at=now,
            )
        )

    async def _persist_review(
        self,
        *,
        review: ReviewBundle,
        owner_id: str,
        device_id: str,
        now: datetime,
    ) -> str:
        provenance = review.provenance[0]
        try:
            bundle_sha256 = await self._privacy_store.put_review_bundle(review)
            await self._privacy_store.append_audit(
                PrivacyAuditEvent(
                    event_id=self._id_factory(),
                    event_type=PrivacyAuditEventType.PACKET_SENT,
                    owner_hash=_identifier_hash(owner_id),
                    device_hash=_identifier_hash(device_id),
                    object_hash=bundle_sha256,
                    count=review.fact_count,
                    data_types=provenance.data_types,
                    capabilities=(DeviceCapability.HEALTH_REVIEW_SUBMIT,),
                    decision=AuditDecision.ALLOW,
                    result=AuditResult.SUCCESS,
                    reason_code="HEALTH_REVIEW_STORED",
                    occurred_at=now,
                )
            )
        except ValueError as exc:
            raise HealthIngestionError("HEALTH_REVIEW_INVALID", status_code=422) from exc
        return bundle_sha256


def build_health_ingestion_service(
    *,
    store_group: StoreGroup,
) -> HealthIngestionService:
    """从既有 StoreGroup 构造唯一 health ingestion service。"""

    return HealthIngestionService(
        device_store=store_group.device_trust_store,
        privacy_store=store_group.privacy_ingestion_store,
        options=HealthIngestionServiceOptions(),
    )


__all__ = [
    "HealthAnalysisAccepted",
    "HealthAnalysisRequest",
    "HealthIngestionError",
    "HealthIngestionService",
    "HealthIngestionServiceOptions",
    "HealthRequestContext",
    "HealthReviewAccepted",
    "build_health_ingestion_service",
]
