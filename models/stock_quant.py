# -*- coding: utf-8 -*-
from odoo import api, models

ACTIVE_WORKSHOP_STATES = (
    'validated',
    'confirmed',
    'sent_to_workshop',
    'in_progress',
    'partial_done',
)

SALE_LINKED_INPUT_STATES = (
    'pending',
    'reserved_for_workshop',
    'sent_to_workshop',
    'in_progress',
    'partial_done',
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
            ('state', 'not in', ('cancelled', 'done', 'rejected', 'damaged')),
        ])
        for line in lines:
            order = line.order_id
            if order.state in ACTIVE_WORKSHOP_STATES:
                committed_ids.add(line.lot_id.id)
                continue
            if order.sale_order_id and line.state in SALE_LINKED_INPUT_STATES:
                committed_ids.add(line.lot_id.id)

        selections = self.env['sale.stone.workshop.input.selection'].search([
            ('base_product_id', '=', int(product_id)),
            ('lot_id', '!=', False),
            ('state', 'in', SALE_WORKSHOP_SELECTION_ACTIVE_STATES),
            ('sale_order_id.state', 'in', ('sale', 'done')),
        ])
        committed_ids.update(selections.mapped('lot_id').ids)

        return list(committed_ids)
