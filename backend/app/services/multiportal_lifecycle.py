from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.client import Client
from app.models.integration_log import IntegrationLog
from app.models.tracker import Tracker
from app.models.vehicle import Vehicle
from app.services.multiportal import CallResult, MultiportalError, multiportal_service


EXTERNAL_LINK_STATES = {'sincronizado', 'erro'}


@dataclass
class LifecycleCall:
    result: CallResult
    entity_type: str
    entity_id: int | None
    phase: str
    tracker_id: int | None = None


@dataclass
class LifecycleResult:
    managed_externally: bool = False
    calls: list[LifecycleCall] = field(default_factory=list)


class LifecycleSyncError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        calls: list[LifecycleCall] | None = None,
        compensation_failed: bool = False,
        http_status: int = 502,
    ) -> None:
        super().__init__(message)
        self.calls = calls or []
        self.compensation_failed = compensation_failed
        self.http_status = http_status


def _requires_external_cleanup(tracker: Tracker) -> bool:
    status = (tracker.integration_status or '').strip().lower()
    if status in EXTERNAL_LINK_STATES:
        return True
    # Registros históricos podem não ter integration_status mesmo já existindo
    # no provedor. Com a integração ativa e uma referência externa completa,
    # o unlink idempotente é mais seguro do que presumir que nunca houve vínculo.
    has_external_reference = bool(
        tracker.external_manufacturer_id
        and (tracker.serial_number or tracker.imei)
    )
    return multiportal_service.enabled and has_external_reference


def _ensure_available(trackers: list[Tracker]) -> bool:
    requires_external = any(_requires_external_cleanup(tracker) for tracker in trackers)
    if requires_external and not multiportal_service.enabled:
        raise LifecycleSyncError(
            'O vínculo externo precisa ser removido, mas a integração Multiportal está indisponível.',
            http_status=503,
        )
    if (
        requires_external
        and settings.multiportal_wsdl_url.strip().lower().startswith('http://')
        and not settings.multiportal_allow_insecure_http
    ):
        raise LifecycleSyncError(
            'A operação foi bloqueada porque o Multiportal está configurado sem TLS.',
            http_status=503,
        )
    return requires_external


def _call(
    calls: list[LifecycleCall],
    *,
    entity_type: str,
    entity_id: int | None,
    phase: str,
    tracker_id: int | None,
    operation,
) -> CallResult:
    try:
        result = operation()
    except MultiportalError as exc:
        raise LifecycleSyncError(
            'O Multiportal recusou a operação de ciclo de vida.',
            calls=calls,
        ) from exc
    calls.append(
        LifecycleCall(
            result=result,
            entity_type=entity_type,
            entity_id=entity_id,
            phase=phase,
            tracker_id=tracker_id,
        )
    )
    return result


def _restore_equipment_links(
    calls: list[LifecycleCall],
    trackers: list[Tracker],
    vehicle: Vehicle,
    *,
    phase: str,
) -> bool:
    compensation_failed = False
    for tracker in trackers:
        try:
            result = _call(
                calls,
                entity_type='tracker',
                entity_id=tracker.id,
                tracker_id=tracker.id,
                phase=phase,
                operation=lambda tracker=tracker: multiportal_service.link_equipment_vehicle(tracker, vehicle),
            )
            compensation_failed = compensation_failed or not result.success
        except Exception:
            # Continua tentando restaurar os demais equipamentos; o chamador
            # receberá a indicação inequívoca de que reconciliação é necessária.
            compensation_failed = True
    return compensation_failed


def unlink_vehicle_assignments(
    *,
    trackers: list[Tracker],
    vehicle: Vehicle,
    client: Client,
) -> LifecycleResult:
    managed_trackers = [tracker for tracker in trackers if _requires_external_cleanup(tracker)]
    if not _ensure_available(managed_trackers):
        return LifecycleResult()

    calls: list[LifecycleCall] = []
    try:
        for tracker in managed_trackers:
            result = _call(
                calls,
                entity_type='tracker',
                entity_id=tracker.id,
                tracker_id=tracker.id,
                phase='unlink_equipment',
                operation=lambda tracker=tracker: multiportal_service.unlink_equipment_vehicle(tracker, vehicle),
            )
            if not result.success:
                raise LifecycleSyncError(
                    'O Multiportal não confirmou o desvínculo do equipamento.',
                    calls=calls,
                )

        vehicle_result = _call(
            calls,
            entity_type='vehicle',
            entity_id=vehicle.id,
            tracker_id=None,
            phase='unlink_vehicle_client',
            operation=lambda: multiportal_service.unlink_vehicle_client(vehicle, client),
        )
        if not vehicle_result.success:
            raise LifecycleSyncError(
                'O Multiportal não confirmou o desvínculo entre veículo e cliente.',
                calls=calls,
            )
    except Exception as exc:
        # Uma resposta de erro/timeout não prova que o provedor deixou de
        # executar a mutação. Reconstituímos o estado original inteiro de modo
        # idempotente: veículo-cliente e TODOS os equipamentos. Assim o banco
        # local pode permanecer instalado sem divergir silenciosamente.
        compensation_calls, compensation_failed = compensate_successful_uninstall(
            trackers=managed_trackers,
            vehicle=vehicle,
            client=client,
        )
        calls.extend(compensation_calls)
        if isinstance(exc, LifecycleSyncError):
            message = str(exc)
            http_status = exc.http_status
            compensation_failed = compensation_failed or exc.compensation_failed
        else:
            message = 'Falha inesperada ao remover o vínculo no Multiportal.'
            http_status = 502
        raise LifecycleSyncError(
            message,
            calls=calls,
            compensation_failed=compensation_failed,
            http_status=http_status,
        ) from exc

    return LifecycleResult(managed_externally=True, calls=calls)


def transfer_tracker_assignment(
    *,
    tracker: Tracker,
    old_vehicle: Vehicle,
    new_vehicle: Vehicle,
    new_client: Client,
) -> LifecycleResult:
    if not _ensure_available([tracker]):
        return LifecycleResult()

    calls: list[LifecycleCall] = []
    unlink_attempted = False
    try:
        # Marcado antes da chamada: se a resposta se perder, o provedor pode
        # ter removido o vínculo mesmo que recebamos erro 99/timeout.
        unlink_attempted = True
        unlink_result = _call(
            calls,
            entity_type='tracker',
            entity_id=tracker.id,
            tracker_id=tracker.id,
            phase='unlink_old_equipment',
            operation=lambda: multiportal_service.unlink_equipment_vehicle(tracker, old_vehicle),
        )
        if not unlink_result.success:
            raise LifecycleSyncError(
                'O Multiportal não confirmou o desvínculo do veículo anterior.',
                calls=calls,
            )
        # Não usamos full_sync_for_tracker aqui: ele também sincroniza a conta
        # de usuário e pode gerar nova senha/e-mail de boas-vindas. Transferir
        # equipamento não tem autorização para alterar credenciais do cliente.
        destination_steps = (
            ('client', new_client.id, 'sync_new_client',
             lambda: multiportal_service.sync_client(new_client, None)),
            ('vehicle', new_vehicle.id, 'sync_new_vehicle', lambda: multiportal_service.sync_vehicle(new_vehicle)),
            ('tracker', tracker.id, 'sync_new_equipment', lambda: multiportal_service.sync_equipment(tracker)),
            (
                'vehicle', new_vehicle.id, 'link_new_vehicle_client',
                lambda: multiportal_service.link_vehicle_client(new_vehicle, new_client),
            ),
            (
                'tracker', tracker.id, 'link_new_equipment_vehicle',
                lambda: multiportal_service.link_equipment_vehicle(tracker, new_vehicle),
            ),
        )
        for entity_type, entity_id, phase, operation in destination_steps:
            result = _call(
                calls,
                entity_type=entity_type,
                entity_id=entity_id,
                tracker_id=tracker.id if entity_type == 'tracker' else None,
                phase=phase,
                operation=operation,
            )
            if not result.success:
                raise LifecycleSyncError(
                    'O Multiportal não confirmou todas as etapas do novo vínculo.',
                    calls=calls,
                )
    except Exception as exc:
        lifecycle_error = exc if isinstance(exc, LifecycleSyncError) else LifecycleSyncError(
            'Falha inesperada ao transferir o vínculo no Multiportal.',
            calls=calls,
        )
        compensation_failed = lifecycle_error.compensation_failed
        if unlink_attempted:
            try:
                # A remoção no destino é idempotente e evita manter dois vínculos
                # caso a resposta da última etapa tenha se perdido na rede.
                cleanup = _call(
                    calls,
                    entity_type='tracker',
                    entity_id=tracker.id,
                    tracker_id=tracker.id,
                    phase='compensate_unlink_new_equipment',
                    operation=lambda: multiportal_service.unlink_equipment_vehicle(tracker, new_vehicle),
                )
                compensation_failed = compensation_failed or not cleanup.success
            except Exception:
                compensation_failed = True
            try:
                restore = _call(
                    calls,
                    entity_type='tracker',
                    entity_id=tracker.id,
                    tracker_id=tracker.id,
                    phase='compensate_relink_old_equipment',
                    operation=lambda: multiportal_service.link_equipment_vehicle(tracker, old_vehicle),
                )
                compensation_failed = compensation_failed or not restore.success
            except Exception:
                compensation_failed = True
        raise LifecycleSyncError(
            str(lifecycle_error),
            calls=calls,
            compensation_failed=compensation_failed,
            http_status=lifecycle_error.http_status,
        ) from exc

    return LifecycleResult(managed_externally=True, calls=calls)


def compensate_successful_uninstall(
    *,
    trackers: list[Tracker],
    vehicle: Vehicle,
    client: Client,
) -> tuple[list[LifecycleCall], bool]:
    calls: list[LifecycleCall] = []
    compensation_failed = False
    try:
        result = _call(
            calls,
            entity_type='vehicle',
            entity_id=vehicle.id,
            tracker_id=None,
            phase='rollback_relink_vehicle_client',
            operation=lambda: multiportal_service.link_vehicle_client(vehicle, client),
        )
        compensation_failed = not result.success
    except Exception:
        compensation_failed = True
    compensation_failed = (
        _restore_equipment_links(calls, trackers, vehicle, phase='rollback_relink_equipment')
        or compensation_failed
    )
    return calls, compensation_failed


def compensate_successful_transfer(
    *,
    tracker: Tracker,
    old_vehicle: Vehicle,
    new_vehicle: Vehicle,
) -> tuple[list[LifecycleCall], bool]:
    calls: list[LifecycleCall] = []
    compensation_failed = False
    try:
        result = _call(
            calls,
            entity_type='tracker',
            entity_id=tracker.id,
            tracker_id=tracker.id,
            phase='rollback_unlink_new_equipment',
            operation=lambda: multiportal_service.unlink_equipment_vehicle(tracker, new_vehicle),
        )
        compensation_failed = not result.success
    except Exception:
        compensation_failed = True
    try:
        result = _call(
            calls,
            entity_type='tracker',
            entity_id=tracker.id,
            tracker_id=tracker.id,
            phase='rollback_relink_old_equipment',
            operation=lambda: multiportal_service.link_equipment_vehicle(tracker, old_vehicle),
        )
        compensation_failed = compensation_failed or not result.success
    except Exception:
        compensation_failed = True
    return calls, compensation_failed


def add_lifecycle_logs(db: Session, calls: list[LifecycleCall], *, batch_id: str | None = None) -> str:
    resolved_batch_id = batch_id or uuid4().hex
    for call in calls:
        db.add(
            IntegrationLog(
                provider='multiportal',
                batch_id=resolved_batch_id,
                entity_type=call.entity_type,
                entity_id=call.entity_id,
                operation=call.result.operation,
                transaction_id=call.result.transaction_id,
                success=call.result.success,
                response_code=call.result.status_code,
                response_description=call.result.status_description,
                request_payload={'lifecycle_phase': call.phase},
                response_payload=call.result.response_payload,
            )
        )
    return resolved_batch_id


def apply_tracker_integration_result(tracker: Tracker, calls: list[LifecycleCall], *, status: str) -> None:
    own_calls = [call for call in calls if call.tracker_id == tracker.id and not call.phase.startswith('compensate')]
    last = own_calls[-1].result if own_calls else None
    tracker.integration_status = status
    if last:
        tracker.integration_last_code = last.status_code
        tracker.integration_last_description = last.status_description
        tracker.integration_last_transaction_id = last.transaction_id
