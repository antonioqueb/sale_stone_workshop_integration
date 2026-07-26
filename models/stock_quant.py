# -*- coding: utf-8 -*-
from odoo import api, models

ACTIVE_WORKSHOP_STATES = (
    'in_workshop',
)

SALE_LINKED_INPUT_STATES = (
    'pending',
    'reserved_for_workshop',
    'in_progress',
)

SALE_WORKSHOP_SELECTION_ACTIVE_STATES = (
    'selected',
    'reserved',
    'moved_to_workshop',
)


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def _get_committed_lot_ids(self, product_id):
        """
        Extiende el selector visual de venta para que también considere como
        comprometidas:
        - placas base ya seleccionadas/reservadas en órdenes de taller,
        - placas base seleccionadas desde venta aunque todavía no exista OT.

        Esto evita que otro vendedor tome A Mate cuando ya fue apartado para
        producir A Pulido en una orden de venta distinta.
        """
        committed_ids = set(super()._get_committed_lot_ids(product_id))

        lines = self.env['workshop.input.line'].search([
            ('product_id', '=', int(product_id)),
            ('lot_id', '!=', False),
            ('state', 'not in', ('cancelled', 'done', 'rejected')),
        ])
        for line in lines:
            order = line.order_id
            if order.state in ACTIVE_WORKSHOP_STATES:
                committed_ids.add(line.lot_id.id)
                continue
            # Solo OTs en borrador comprometen por venta. Una línea 'pending'
            # de una OT terminada (placa devuelta como no usada al declarar el
            # resultado) o cancelada NO debe seguir bloqueando el lote: la
            # placa ya está físicamente de vuelta en el stock disponible.
            if (
                order.sale_order_id
                and order.state == 'draft'
                and line.state in SALE_LINKED_INPUT_STATES
            ):
                committed_ids.add(line.lot_id.id)

        selections = self.env['sale.stone.workshop.input.selection'].search([
            ('base_product_id', '=', int(product_id)),
            ('lot_id', '!=', False),
            ('state', 'in', SALE_WORKSHOP_SELECTION_ACTIVE_STATES),
            ('sale_order_id.state', 'in', ('sale', 'done')),
        ])
        committed_ids.update(selections.mapped('lot_id').ids)

        return list(committed_ids)
