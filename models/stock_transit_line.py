# -*- coding: utf-8 -*-
"""Puente Taller ↔ Transit Allocation.

Una línea de tránsito puede reservarse para la DEMANDA DE TALLER de una
línea de venta (material de origen a transformar). No se usa order_id /
sale_line_id porque eso dispararía STONE SYNC y el ratchet contra el
producto VENDIDO — que es otro producto distinto al material base.
"""
from odoo import fields, models


class StockTransitLineWorkshop(models.Model):
    _inherit = 'stock.transit.line'

    workshop_sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Línea de venta (taller)',
        index=True,
        copy=False,
        ondelete='set null',
        help='Línea de venta cuya demanda de TALLER reservó este material '
             'en tránsito (producto origen a transformar). La reserva se '
             'hace desde Transit Allocation con el filtro Taller.',
    )
