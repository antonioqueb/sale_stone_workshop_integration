# -*- coding: utf-8 -*-
"""Recepción → reserva de taller.

Material asignado a TALLER cuando venía EN TRÁNSITO (filtro Taller de
Transit Allocation): la selección y hasta la línea de entrada de la OT
podían existir, pero la RESERVA FÍSICA no podía tomarse porque el lote
aún no tenía quants internos. Al validarse la recepción que lo vuelve
stock, nadie re-empujaba la selección ni refrescaba la reserva — el lote
quedaba LIBRE en el visual y vendible (caso S78 / V/478 /
T-TALLER/2026/0005). Este hook cierra ese hueco.
"""
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def button_validate(self):
        res = super().button_validate()
        # Cosmético-defensivo: el hueco se repara, pero jamás debe tumbar
        # una validación de recepción ya hecha.
        try:
            self._sale_workshop_resync_received_selections()
        except Exception:
            _logger.exception(
                '[SALE WORKSHOP] Fallo re-sincronizando selecciones de '
                'taller al validar %s', self.mapped('name'))
        return res

    def _sale_workshop_resync_received_selections(self):
        if self.env.context.get('skip_sale_workshop_reception_resync'):
            return
        done = self.filtered(
            lambda p: p.state == 'done'
            and p.picking_type_code in ('incoming', 'internal'))
        if not done:
            return
        lot_ids = done.mapped('move_line_ids.lot_id').ids
        if not lot_ids:
            return
        Selection = self.env['sale.stone.workshop.input.selection'].sudo()
        sels = Selection.search([
            ('lot_id', 'in', lot_ids),
            ('state', 'in', ('selected', 'reserved')),
            ('sale_order_id.state', 'in', ('sale', 'done')),
        ])
        for line in sels.mapped('sale_line_id'):
            workshop = line.stone_workshop_order_id
            if not workshop or workshop.state not in ('draft', 'validated'):
                continue
            line.sudo().with_context(
                skip_sale_workshop_reception_resync=True,
            )._stone_workshop_push_input_selections_to_workshop(workshop)
            _logger.info(
                '[SALE WORKSHOP] Selecciones de %s re-empujadas a %s tras '
                'recepción (reserva física refrescada).',
                line.order_id.name, workshop.name)
