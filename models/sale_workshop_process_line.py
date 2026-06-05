# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleStoneWorkshopProcessLine(models.Model):
    _name = 'sale.stone.workshop.process.line'
    _description = 'Proceso adicional de taller encadenado desde venta'
    _order = 'sale_line_id, sequence, id'
    _rec_name = 'name'

    sequence = fields.Integer(default=10)

    sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Línea de venta',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sale_order_id = fields.Many2one(
        related='sale_line_id.order_id',
        string='Orden de venta',
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        related='sale_line_id.company_id',
        string='Compañía',
        store=True,
        readonly=True,
    )
    process_id = fields.Many2one(
        'workshop.process',
        string='Proceso',
        required=True,
        help='Servicio de taller que se aplica en este paso de la cadena.',
    )
    input_product_id = fields.Many2one(
        'product.product',
        string='Producto que consume',
        required=True,
        domain=[('tracking', '!=', 'none')],
        help=(
            'Producto intermedio que este proceso recibe del paso anterior. '
            'El resultado de este proceso lo consume el siguiente paso, y el último '
            'paso produce el producto vendido.'
        ),
    )
    workshop_order_id = fields.Many2one(
        'workshop.order',
        string='Orden de taller',
        readonly=True,
        copy=False,
        ondelete='set null',
        help='Orden de taller generada para este paso al confirmar la venta.',
    )
    name = fields.Char(
        string='Descripción',
        compute='_compute_name',
        store=True,
    )

    @api.depends('process_id', 'input_product_id')
    def _compute_name(self):
        for line in self:
            process = line.process_id.display_name or ''
            product = line.input_product_id.display_name or ''
            if process and product:
                line.name = '%s ← %s' % (process, product)
            else:
                line.name = process or product or '/'
