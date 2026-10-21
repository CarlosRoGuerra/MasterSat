-- Caso do vídeo de 21/10/2026: Fortmais, boleto 38709, pago pela Acquafort.
-- Não transfere o contrato nem o cliente atendido da cobrança.
-- Pagador já correto: nenhuma escrita. Pagador ausente: completa o snapshot.
-- Pagador diferente, cliente ambíguo ou boleto diferente: aborta a transação.
-- IDs conferidos na VPS em 21/10/2026: Fortmais 159, Acquafort 346.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $repair$
DECLARE
    alvo billings%ROWTYPE;
    dono_id integer;
    pagador_id integer;
BEGIN
    SELECT id INTO STRICT dono_id FROM clients
    WHERE id = 159 AND is_deleted = false
      AND upper(trim(name)) LIKE 'FORTMAIS%'
    FOR SHARE;

    SELECT id INTO STRICT pagador_id FROM clients
    WHERE id = 346 AND is_deleted = false
      AND upper(trim(name)) LIKE 'ACQUAFORT%'
    FOR SHARE;

    SELECT * INTO STRICT alvo FROM billings WHERE id = 38709 FOR UPDATE;

    IF alvo.is_deleted OR alvo.client_id <> dono_id
       OR alvo.status::text <> 'PAID'
       OR alvo.amount <> 649.90 OR alvo.due_date <> DATE '2026-10-15'
       OR alvo.substituted_by_id IS NOT NULL THEN
        RAISE EXCEPTION 'Boleto 38709 não corresponde ao título pago da Fortmais mostrado no vídeo. Nenhuma alteração aplicada.';
    END IF;

    IF alvo.payer_client_id = pagador_id THEN
        RAISE NOTICE 'O boleto 38709 já tem a Acquafort como pagadora. O problema é o filtro da tela: atualize o sistema. Nenhuma alteração no banco.';
        RETURN;
    END IF;

    IF alvo.payer_client_id IS NOT NULL THEN
        RAISE EXCEPTION 'O boleto 38709 tem outro pagador explícito (%). Confira a consulta antes de alterar o histórico.', alvo.payer_client_id;
    END IF;

    UPDATE billings
    SET payer_client_id = pagador_id, updated_at = CURRENT_TIMESTAMP
    WHERE id = alvo.id AND payer_client_id IS NULL;

    INSERT INTO billing_change_logs
        (billing_id, changed_by_user_id, field_name, previous_value, new_value, justification)
    VALUES
        (alvo.id, NULL, 'payer_client_id', NULL, pagador_id::text,
         'Saneamento pontual solicitado pelo operador: vídeo de 21/10/2026 confirma a Acquafort como pagadora do boleto 38709 da Fortmais. Snapshot ausente preenchido; cliente atendido, valores e pagamento preservados.');

    RAISE NOTICE 'Snapshot do pagador preenchido no boleto 38709. Cliente atendido, contrato, valor e pagamento preservados.';
END;
$repair$;

SELECT b.id AS boleto, dono.name AS cliente_atendido,
       pagador.name AS responsavel_financeiro,
       b.payer_client_id, b.amount AS valor, b.status,
       b.due_date AS vencimento, b.payment_date AS pagamento
FROM billings b
JOIN clients dono ON dono.id = b.client_id
JOIN clients pagador ON pagador.id = COALESCE(b.payer_client_id, b.client_id)
WHERE b.id = 38709;

COMMIT;
