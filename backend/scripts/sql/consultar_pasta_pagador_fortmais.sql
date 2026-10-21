-- Somente leitura. IDs conferidos na VPS: Fortmais 159, Acquafort 346.
-- A grafia do nome no cadastro não coincide com a usada no vídeo.
-- Cliente atendido e responsável financeiro são vínculos distintos.
-- Primeiro consulta o título mostrado no vídeo, mesmo que tenha sido removido depois.
SELECT b.id AS boleto, dono.id AS cliente_id, dono.name AS cliente_atendido,
       b.payer_client_id, pagador.id AS pagador_efetivo_id,
       pagador.name AS responsavel_financeiro,
       b.contract_id, c.interveniente_client_id AS interveniente_atual_id,
       atual.name AS interveniente_atual,
       b.billing_type, b.amount AS valor, b.status, b.is_deleted,
       b.due_date AS vencimento, b.payment_date AS pagamento,
       b.paid_amount AS valor_pago, b.receipt_number AS recibo,
       b.substituted_by_id AS incluido_no_boleto
FROM billings b
JOIN clients dono ON dono.id = b.client_id
JOIN clients pagador ON pagador.id = COALESCE(b.payer_client_id, b.client_id)
LEFT JOIN contracts c ON c.id = b.contract_id
LEFT JOIN clients atual ON atual.id = c.interveniente_client_id
WHERE b.id = 38709;

-- Esta é a mesma regra da pasta corrigida: boleto efetivo do pagador,
-- incluindo títulos pagos e títulos cujo contrato pertence a outro cliente.
SELECT b.id AS boleto, dono.name AS cliente_atendido,
       pagador.name AS responsavel_financeiro,
       b.amount AS valor, b.status,
       b.due_date AS vencimento, b.payment_date AS pagamento
FROM billings b
JOIN clients dono ON dono.id = b.client_id
JOIN clients pagador ON pagador.id = COALESCE(b.payer_client_id, b.client_id)
WHERE b.is_deleted = false AND b.substituted_by_id IS NULL
  AND pagador.id = 346
ORDER BY b.due_date DESC, b.id DESC;
