"""Placas devueltas sin procesar que quedaron 'Movida a taller'.

El cierre de la OT dejaba su línea de entrada en 'done' y la liberación de
selecciones (que buscaba 'pending') nunca corría: la placa física quedaba
libre y sin reserva, pero la venta la seguía viendo en taller y no podía
crear otra OT (caso V/306, T-TALLER/2026/0013: 25 de 45 placas).
Se aplica la regla nueva a las OTs terminadas: si la línea aún debe
producto, las placas quedan 'Seleccionada' con la venta para una OT de
seguimiento; si no, se liberan.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env['workshop.order'].search([
        ('state', '=', 'done'),
        ('sale_line_id', '!=', False),
    ])
    touched = orders.filtered(lambda o: o.sale_workshop_input_selection_ids.filtered(
        lambda s: s.state != 'cancelled'
        and s.workshop_input_line_id
        and not s.workshop_input_line_id.is_consumed
        and s.workshop_input_line_id.return_picking_id))
    for order in touched:
        with cr.savepoint():
            order._sale_workshop_release_unused_selections()
    # Placas ya transformadas: de "Movida a taller" a "Procesada en taller".
    for order in orders:
        with cr.savepoint():
            order.sale_workshop_input_selection_ids._sync_state_from_workshop_input()
    _logger.info('[sale_stone_workshop_integration] OTs con placas no usadas saneadas: %s',
                 touched.mapped('name'))
