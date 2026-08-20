from __future__ import annotations

import secrets
import string
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests
from zeep import Client, Settings
from zeep.helpers import serialize_object
from zeep.transports import Transport

from app.core.config import settings
from app.models.client import Client as LocalClient
from app.models.tracker import Tracker as LocalTracker
from app.models.user import User as LocalUser
from app.models.vehicle import Vehicle as LocalVehicle


SUCCESS_CODES = {'0', '200'}
IDEMPOTENT_CODES = {'20', '21', '22', '40', '56', '59'}
SOFT_SUCCESS_CODES = SUCCESS_CODES | IDEMPOTENT_CODES

VEHICLE_TYPE_MAP = {
    'passeio': 1,
    'carro': 1,
    'automovel': 1,
    'caminhao': 2,
    'caminhão': 2,
    'van': 3,
    'moto': 4,
    'motocicleta': 4,
    'onibus': 5,
    'ônibus': 5,
}

SIM_STATUS_MAP = {
    'ativo': 1,
    'active': 1,
    'bloqueado': 2,
    'block': 2,
    'cancelado': 3,
    'cancelled': 3,
    'suspenso': 4,
    'suspended': 4,
}

LOGRADOURO_MAP = {
    'RUA': 'R',
    'AVENIDA': 'AV',
    'AV': 'AV',
    'RODOVIA': 'ROD',
    'ESTRADA': 'ESTR',
    'TRAVESSA': 'TV',
    'ALAMEDA': 'AL',
    'PRAÇA': 'PCA',
    'PRACA': 'PCA',
}


class MultiportalError(Exception):
    pass


@dataclass
class CallResult:
    operation: str
    transaction_id: str | None
    status_code: str | None
    status_description: str | None
    success: bool
    response_payload: dict | list | None

    def as_dict(self) -> dict[str, Any]:
        return {
            'operation': self.operation,
            'transaction_id': self.transaction_id,
            'status_code': self.status_code,
            'status_description': self.status_description,
            'success': self.success,
            'response_payload': self.response_payload,
        }


class MultiportalService:
    def __init__(self):
        self._client: Client | None = None

    @property
    def enabled(self) -> bool:
        return settings.multiportal_enabled and bool(settings.multiportal_id and settings.multiportal_password and settings.multiportal_wsdl_url)

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise MultiportalError('Integração Multiportal não está habilitada ou está sem credenciais configuradas.')

    def _get_client(self) -> Client:
        if self._client is None:
            self._ensure_enabled()
            session = requests.Session()
            transport = Transport(session=session, timeout=settings.multiportal_request_timeout)
            self._client = Client(
                settings.multiportal_wsdl_url,
                transport=transport,
                settings=Settings(strict=False, xml_huge_tree=True),
            )
        return self._client

    def _next_transaction_id(self) -> str:
        # idTransacao é Java Long (max 19 dígitos) — não incluir microssegundos
        from datetime import timezone
        return datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')

    def _call(self, operation: str, **params: Any) -> CallResult:
        client = self._get_client()
        transaction_id = str(params.get('idTransacao') or self._next_transaction_id())
        params['idTransacao'] = transaction_id
        try:
            func = getattr(client.service, operation)
            raw_response = func(**params)
            payload = serialize_object(raw_response)
        except Exception as exc:  # noqa: BLE001
            return CallResult(
                operation=operation,
                transaction_id=transaction_id,
                status_code='99',
                status_description=str(exc),
                success=False,
                response_payload={'error': str(exc)},
            )

        payload = payload or {}
        status_code = str(payload.get('statusCode') if isinstance(payload, dict) else None)
        if status_code == 'None':
            status_code = None
        status_description = payload.get('statusDescription') if isinstance(payload, dict) else None
        success = status_code in SOFT_SUCCESS_CODES
        return CallResult(
            operation=operation,
            transaction_id=transaction_id,
            status_code=status_code,
            status_description=status_description,
            success=success,
            response_payload=payload,
        )

    def list_manufacturers(self) -> list[dict[str, str]]:
        self._ensure_enabled()
        client = self._get_client()
        try:
            response = client.service.listarFabricantes(id=settings.multiportal_id, senha=settings.multiportal_password)
            payload = serialize_object(response)
        except Exception as exc:  # noqa: BLE001
            raise MultiportalError(f'Falha ao listar fabricantes: {exc}') from exc

        if isinstance(payload, dict):
            elements = payload.get('elements') or payload.get('Dominio') or payload.get('return') or []
        else:
            elements = payload or []

        result: list[dict[str, str]] = []
        for item in elements:
            if isinstance(item, dict):
                code = str(item.get('codigo') or item.get('code') or '')
                description = str(item.get('descricao') or item.get('description') or '')
                if code or description:
                    result.append({'code': code, 'description': description})
        return result

    def sync_client(self, local_client: LocalClient, linked_user: LocalUser | None = None, *, operation_code: int = 1) -> CallResult:
        payload = self._build_client_payload(local_client, linked_user)
        result = self._call(
            'sincronizaCliente',
            id=settings.multiportal_id,
            senha=settings.multiportal_password,
            codigoOperacao=operation_code,
            cliente=payload,
        )
        if result.status_code == '20' and operation_code == 1:
            return self.sync_client(local_client, linked_user, operation_code=4)
        return result

    def sync_user(self, local_client: LocalClient, linked_user: LocalUser, *, operation_code: int = 1) -> CallResult:
        payload = self._build_user_payload(local_client, linked_user)
        result = self._call(
            'sincronizaUsuario',
            id=settings.multiportal_id,
            senha=settings.multiportal_password,
            codigoOperacao=operation_code,
            usuario=payload,
        )
        if result.status_code == '40' and operation_code == 1:
            return self.sync_user(local_client, linked_user, operation_code=2)
        return result

    def sync_vehicle(self, vehicle: LocalVehicle, *, operation_code: int = 1) -> CallResult:
        payload = self._build_vehicle_payload(vehicle)
        result = self._call(
            'sincronizaVeiculo',
            id=settings.multiportal_id,
            senha=settings.multiportal_password,
            codigoOperacao=operation_code,
            veiculo=payload,
        )
        if result.status_code == '21' and operation_code == 1:
            return self.sync_vehicle(vehicle, operation_code=2)
        return result

    def sync_equipment(self, tracker: LocalTracker, *, operation_code: int = 1) -> CallResult:
        payload = self._build_equipment_payload(tracker)
        result = self._call(
            'sincronizaEquipamento',
            id=settings.multiportal_id,
            senha=settings.multiportal_password,
            codigoOperacao=operation_code,
            equipamento=payload,
        )
        if result.status_code in {'22', '38'} and operation_code == 1:
            return self.sync_equipment(tracker, operation_code=2)
        return result

    def link_vehicle_client(self, vehicle: LocalVehicle, local_client: LocalClient, when: datetime | None = None) -> CallResult:
        result = self._call(
            'vinculoVeiculoCliente',
            id=settings.multiportal_id,
            senha=settings.multiportal_password,
            codigoOperacao=1,
            chassi=(vehicle.chassis or '').strip(),
            cliente=self._build_client_reference(local_client),
            data=(when or datetime.utcnow()).strftime('%Y%m%d%H%M%S'),
        )
        return result

    def link_equipment_vehicle(self, tracker: LocalTracker, vehicle: LocalVehicle, when: datetime | None = None) -> CallResult:
        result = self._call(
            'vinculoEquipamentoVeiculo',
            id=settings.multiportal_id,
            senha=settings.multiportal_password,
            codigoOperacao=1,
            chassi=(vehicle.chassis or '').strip(),
            equipamento=self._build_equipment_reference(tracker),
            data=(when or datetime.utcnow()).strftime('%Y%m%d%H%M%S'),
        )
        return result

    def sync_chip_status(self, tracker: LocalTracker, chip_status: int) -> CallResult:
        """sincronizaChip — altera status do chip pelo ICCID ou serial+fabricante."""
        params: dict[str, Any] = {
            'id': settings.multiportal_id,
            'senha': settings.multiportal_password,
            'statusChip': chip_status,
        }
        if tracker.sim_iccid:
            params['serialChip'] = tracker.sim_iccid.strip()
        elif tracker.serial_number or tracker.imei:
            serial = (tracker.serial_number or tracker.imei).strip()
            if not tracker.external_manufacturer_id:
                raise MultiportalError('Rastreador sem fabricante externo para alterar status do chip.')
            params['serialEquipamento'] = serial
            params['fabricante'] = tracker.external_manufacturer_id
        else:
            raise MultiportalError('Rastreador sem ICCID ou serial para alterar status do chip.')
        return self._call('sincronizaChip', **params)

    def query_equipment_link(self, *, by_vehicle_chassis: str | None = None, by_serial_and_manufacturer: str | None = None) -> CallResult:
        """consultaVinculoEquipamento — consulta vínculos por chassi ou serial+fabricante."""
        if by_vehicle_chassis:
            return self._call(
                'consultaVinculoEquipamento',
                id=settings.multiportal_id,
                senha=settings.multiportal_password,
                codigoOperacao=2,
                chave=by_vehicle_chassis.strip().upper(),
            )
        if by_serial_and_manufacturer:
            return self._call(
                'consultaVinculoEquipamento',
                id=settings.multiportal_id,
                senha=settings.multiportal_password,
                codigoOperacao=3,
                chave=by_serial_and_manufacturer,
            )
        raise MultiportalError('Informe chassi ou serial+fabricante para consultar o vínculo.')

    def swap_equipment_vehicle(
        self,
        *,
        chassis: str,
        old_tracker: LocalTracker,
        new_tracker: LocalTracker,
        when: datetime | None = None,
    ) -> CallResult:
        """trocaEquipamentoVeiculo — troca equipamento em um veículo em uma chamada."""
        return self._call(
            'trocaEquipamentoVeiculo',
            id=settings.multiportal_id,
            senha=settings.multiportal_password,
            chassi=chassis.strip().upper(),
            equipamentoAntigo=self._build_equipment_reference(old_tracker),
            equipamentoNovo=self._build_equipment_reference(new_tracker),
            data=(when or datetime.utcnow()).strftime('%Y%m%d%H%M%S'),
        )

    def unlink_equipment_vehicle(self, tracker: LocalTracker, vehicle: LocalVehicle, when: datetime | None = None) -> CallResult:
        """vinculoEquipamentoVeiculo codigoOperacao=2 — desvincula equipamento do veículo."""
        return self._call(
            'vinculoEquipamentoVeiculo',
            id=settings.multiportal_id,
            senha=settings.multiportal_password,
            codigoOperacao=2,
            chassi=(vehicle.chassis or '').strip().upper(),
            equipamento=self._build_equipment_reference(tracker),
            data=(when or datetime.utcnow()).strftime('%Y%m%d%H%M%S'),
        )

    def unlink_vehicle_client(self, vehicle: LocalVehicle, local_client: LocalClient, when: datetime | None = None) -> CallResult:
        """vinculoVeiculoCliente codigoOperacao=2 — desvincula veículo do cliente."""
        return self._call(
            'vinculoVeiculoCliente',
            id=settings.multiportal_id,
            senha=settings.multiportal_password,
            codigoOperacao=2,
            chassi=(vehicle.chassis or '').strip(),
            cliente=self._build_client_reference(local_client),
            data=(when or datetime.utcnow()).strftime('%Y%m%d%H%M%S'),
        )

    def full_sync_for_tracker(
        self,
        *,
        tracker: LocalTracker,
        vehicle: LocalVehicle,
        local_client: LocalClient,
        linked_user: LocalUser | None,
    ) -> list[CallResult]:
        results: list[CallResult] = []
        results.append(self.sync_client(local_client, linked_user))
        if linked_user and settings.multiportal_group_codes:
            results.append(self.sync_user(local_client, linked_user))
        results.append(self.sync_vehicle(vehicle))
        results.append(self.sync_equipment(tracker))
        if all(step.success for step in results):
            results.append(self.link_vehicle_client(vehicle, local_client))
        if all(step.success for step in results):
            results.append(self.link_equipment_vehicle(tracker, vehicle))
        return results

    def _build_client_reference(self, local_client: LocalClient) -> dict[str, Any]:
        return {
            'codigoIntegracao': local_client.id,
            'tipoCliente': 'F' if local_client.type == 'pf' else 'J',
            'documento': self._digits(local_client.cpf_cnpj),
            'categoriaCliente': 'C' if local_client.type == 'pf' else 'E',
            'nome': local_client.name,
            'email': (local_client.email or '').strip(),
        }

    def _build_client_payload(self, local_client: LocalClient, linked_user: LocalUser | None) -> dict[str, Any]:
        payload = self._build_client_reference(local_client)
        payload.update(
            {
                'numeroContrato': None,
                'diaVencimentoFatura': None,
                'tempoContrato': None,
                'formaPagamento': None,
            }
        )
        if local_client.type == 'pj':
            payload['nomeFantasia'] = local_client.name

        addresses = self._build_addresses(local_client)
        if addresses:
            payload['listaEnderecos'] = addresses
        contacts = self._build_contacts(local_client)
        if contacts:
            payload['listaContatos'] = contacts
        if linked_user and settings.multiportal_group_codes:
            payload['usuario'] = self._build_user_payload(local_client, linked_user)
        return payload

    def _build_vehicle_payload(self, vehicle: LocalVehicle) -> dict[str, Any]:
        vehicle_type = self._vehicle_type_to_code(vehicle.type)
        if vehicle_type is None:
            raise MultiportalError('Tipo do veículo não pode ser enviado para a Multiportal sem mapeamento válido.')
        chassis = (vehicle.chassis or '').strip().upper()
        if not chassis:
            raise MultiportalError('Veículo sem chassi não pode ser sincronizado com a Multiportal.')
        payload: dict[str, Any] = {
            'codigoIntegracao': vehicle.id,
            'chassi': chassis,
            'placa': (vehicle.plate or '').strip().upper() or None,
            'tipoVeiculo': vehicle_type,
            'apelido': vehicle.contract_number or vehicle.plate,
        }
        # Campos de texto opcionais: omite quando None para não apagar dados já existentes no Multiportal
        if vehicle.brand:
            payload['marca'] = vehicle.brand
        if vehicle.model:
            payload['modelo'] = vehicle.model
        if vehicle.color:
            payload['cor'] = vehicle.color
        # Campos inteiros opcionais: omite quando não preenchidos para evitar envio de 0
        ano_modelo = vehicle.model_year or vehicle.year
        if ano_modelo:
            payload['anoModelo'] = ano_modelo
        if vehicle.manufacture_year:
            payload['anoFabricacao'] = vehicle.manufacture_year
        renavam = self._safe_int(vehicle.renavam)
        if renavam:
            payload['codigoRenavam'] = renavam
        fipe = self._safe_int(vehicle.fipe_code)
        if fipe:
            payload['codigoFipe'] = fipe
        return payload

    def _build_equipment_reference(self, tracker: LocalTracker) -> dict[str, Any]:
        manufacturer_id = tracker.external_manufacturer_id
        if not manufacturer_id:
            raise MultiportalError('Rastreador sem fabricante externo configurado para a Multiportal.')
        return {
            'codigoIntegracao': tracker.id,
            'serialNumber': (tracker.serial_number or tracker.imei).strip(),
            'fabricante': manufacturer_id,
        }

    def _build_equipment_payload(self, tracker: LocalTracker) -> dict[str, Any]:
        manufacturer_id = tracker.external_manufacturer_id
        if not manufacturer_id:
            raise MultiportalError('Rastreador sem fabricante externo configurado para a Multiportal.')
        serial = (tracker.serial_number or tracker.imei).strip()
        if not serial:
            raise MultiportalError('Rastreador sem serialNumber válido para a Multiportal.')
        payload = {
            'codigoIntegracao': tracker.id,
            'serialNumber': serial,
            'fabricante': manufacturer_id,
            'firmware': tracker.firmware,
            'msisdn': tracker.sim_number,
            'iccid': tracker.sim_iccid,
            'statusChip': self._chip_status_to_code(tracker.sim_status),
            'operadora': tracker.carrier,
            'ip': tracker.ip_address,
            'porta': tracker.port,
            'localInstalacao': tracker.install_location,
            'tipoChip': tracker.chip_type,
            'tipoEquipamento': tracker.equipment_type,
            'tipoComunicacao': tracker.communication_type,
            'dtInstalacao': tracker.install_date.strftime('%Y%m%d') if tracker.install_date else None,
        }
        return payload

    @staticmethod
    def _gerar_senha_portal(tamanho: int = 12) -> str:
        """Senha forte e aleatória para a conta do portal do cliente.

        Antes a senha era o próprio login (e-mail/CPF) — previsível e pública.
        A senha gerada é entregue ao cliente pelo e-mail de boas-vindas do
        portal (enviarEmail=True); ele a troca no primeiro acesso.
        """
        alfabeto = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alfabeto) for _ in range(tamanho))

    def _build_user_payload(self, local_client: LocalClient, linked_user: LocalUser) -> dict[str, Any]:
        login = (linked_user.email or local_client.email or self._digits(local_client.cpf_cnpj)).strip().lower()
        if not login:
            raise MultiportalError('Cliente sem login/e-mail válido para criação do usuário no portal Multiportal.')
        return {
            'codigoIntegracao': linked_user.id,
            'login': login,
            'senha': self._gerar_senha_portal(),
            'nome': linked_user.name or local_client.name,
            'tipoUsuario': 'C',
            'codigosGrupo': settings.multiportal_group_codes,
            'documentoCliente': self._digits(local_client.cpf_cnpj),
            'email': local_client.email,
            # Senha aleatória PRECISA chegar ao cliente — força o e-mail de
            # boas-vindas do portal (senão o cliente ficaria sem saber a senha).
            'enviarEmail': True,
        }

    def _build_addresses(self, local_client: LocalClient) -> list[dict[str, Any]]:
        # Este servidor rejeita endereços com latitude/longitude = 0.0 (código 1111),
        # mas aceita clientes sem listaEnderecos (retorna 200).
        # Como o sistema não armazena coordenadas GPS, não enviamos endereço.
        return []

    def _build_contacts(self, local_client: LocalClient) -> list[dict[str, Any]]:
        contacts: list[dict[str, Any]] = []
        if local_client.phone:
            contacts.append(
                {
                    'codigoIntegracao': local_client.id,
                    'nome': local_client.name,
                    'tipoContato': 2,
                    'relacao': 7,
                    'valor': local_client.phone,
                }
            )
        # Additional structured contacts (new contacts JSON field)
        extra_contacts = local_client.contacts or []
        for idx, c in enumerate(extra_contacts, start=1):
            if isinstance(c, dict):
                phone = (c.get('phone') or '').strip()
                email = (c.get('email') or '').strip()
                name = (c.get('name') or local_client.name).strip()
                if phone:
                    contacts.append({
                        'codigoIntegracao': int(f'{local_client.id}9{idx}1'),
                        'nome': name,
                        'tipoContato': 2,
                        'relacao': 7,  # Proprietário — relacao 9 rejeitado pelo servidor
                        'valor': phone,
                    })
                if email:
                    contacts.append({
                        'codigoIntegracao': int(f'{local_client.id}9{idx}2'),
                        'nome': name,
                        'tipoContato': 5,
                        'relacao': 7,  # Proprietário
                        'valor': email,
                    })
        # tipoContato 5 (E-mail) rejeitado por esta versão do servidor.
        # E-mails são enviados no campo `email` do cliente, não como contato separado.
        return contacts

    def _chip_status_to_code(self, value: str | None) -> int | None:
        if not value:
            return 1
        return SIM_STATUS_MAP.get(value.strip().lower(), 1)

    def _vehicle_type_to_code(self, value: str | None) -> int | None:
        if not value:
            return None
        normalized = value.strip().lower()
        return VEHICLE_TYPE_MAP.get(normalized)

    def _logradouro_code(self, line: str) -> str:
        if not line:
            return 'R'
        token = line.strip().split(' ', 1)[0].upper().strip('.,')
        return LOGRADOURO_MAP.get(token, 'R')

    def _digits(self, value: str | None) -> str:
        return ''.join(filter(str.isdigit, value or ''))

    def _safe_int(self, value: str | int | None) -> int | None:
        if value is None or value == '':
            return None
        if isinstance(value, int):
            return value
        digits = self._digits(str(value))
        return int(digits) if digits else None


multiportal_service = MultiportalService()
