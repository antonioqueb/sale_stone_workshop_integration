"""Limpieza de compromisos huérfanos dejados por versiones anteriores.

Los fixes de 19.0.3.4.0/19.0.3.5.0 evitan que se generen nuevos bloqueos,
pero los datos históricos quedaron así en la base:

1. Selecciones de placas base activas cuya OT ya terminó/se canceló y cuya
   placa fue devuelta al stock como "no usada" (línea de entrada 'pending'
   sin consumir) o quedó sin línea de entrada. Seguían contando como
   comprometidas en los selectores de venta/taller aunque la placa está
   físicamente disponible.
2. Selecciones activas de ventas canceladas.
3. OTs en borrador de ventas canceladas: quedaban vivas con su picking de
   reserva bloqueando quants indefinidamente (antes no había cleanup al
   cancelar la venta).

1 y 2 se corrigen por SQL (silencioso, sin chatter). 3 se corrige vía ORM
para que la cancelación libere correctamente el picking de reserva
(_sale_workshop_cancel_input_reservation) igual que el flujo normal.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # 1) Selecciones activas de OTs cerradas cuya placa volvió al stock.
    cr.execute(
        """
        UPDATE sale_stone_workshop_input_selection s
        SET state = 'cancelled'
        FROM workshop_order wo
        WHERE s.workshop_order_id = wo.id
          AND s.state != 'cancelled'
          AND wo.state IN ('done', 'cancel')
          AND (
            s.workshop_input_line_id IS NULL
            OR EXISTS (
                SELECT 1
                FROM workshop_input_line wil
                WHERE wil.id = s.workshop_input_line_id
                  AND wil.state = 'pending'
                  AND COALESCE(wil.is_consumed, FALSE) = FALSE
            )
          )
        """
    )
    released_done = cr.rowcount

    # 2) Selecciones activas de ventas canceladas.
    cr.execute(
        """
        UPDATE sale_stone_workshop_input_selection s
        SET state = 'cancelled'
        FROM sale_order so
        WHERE s.sale_order_id = so.id
          AND s.state != 'cancelled'
          AND so.state = 'cancel'
        """
    )
    released_cancelled_so = cr.rowcount

    # 3) OTs en borrador de ventas canceladas: cancelar vía ORM para liberar
    #    su picking de reserva. A prueba de fallos por orden: un registro
    #    corrupto no debe abortar la actualización del módulo.
    env = api.Environment(cr, SUPERUSER_ID, {})
    stale_workshops = env['workshop.order'].search([
        ('state', '=', 'draft'),
        ('sale_order_id.state', '=', 'cancel'),
    ])
    cancelled_ok = 0
    for workshop in stale_workshops:
        try:
            workshop.action_cancel()
            cancelled_ok += 1
        except Exception:  # noqa: BLE001 - migración best-effort por registro
            _logger.exception(
                '[sale_stone_workshop_integration] No se pudo cancelar la OT '
                'huérfana %s (venta cancelada %s); revisar manualmente.',
                workshop.name,
                workshop.sale_order_id.name,
            )

    _logger.info(
        '[sale_stone_workshop_integration] Limpieza de compromisos huérfanos: '
        '%s selecciones de OTs cerradas, %s selecciones de ventas canceladas, '
        '%s/%s OTs borrador de ventas canceladas.',
        released_done,
        released_cancelled_so,
        cancelled_ok,
        len(stale_workshops),
    )
