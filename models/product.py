# -*- coding: utf-8 -*-
from odoo import fields, models


WORKSHOP_OPERATION_MODE_SELECTION = [
    ('slab_finish', 'Acabado de placas'),
    ('slab_cut', 'Corte de placas'),
    ('format_process', 'Formatos / pallets'),
    ('rework', 'Reproceso / reparación'),
]

WORKSHOP_TRIGGER_SELECTION = [
    ('always', 'Siempre crear taller'),
    ('on_shortage', 'Solo si falta producto final'),
    ('manual', 'Manual'),
]

WORKSHOP_COMMERCIAL_MODE_SELECTION = [
    ('single_line', 'Una línea: precio integrado'),
    ('separate_service', 'Producto + servicio visible'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    stone_workshop_required = fields.Boolean(
        string='Requiere taller para venta',
        help=(
            'Activa la integración de taller para los productos finales que se venden al cliente. '
            'Ejemplo: A Pulido se vende como producto final, pero se puede producir desde A Mate + proceso Pulido.'
        ),
    )
    stone_workshop_base_product_id = fields.Many2one(
        'product.product',
        string='Producto base / insumo',
        domain=[('tracking', '!=', 'none')],
        help='Producto real que se aparta y consume en taller. Ejemplo: A Mate.',
    )
    stone_workshop_process_id = fields.Many2one(
        'workshop.process',
        string='Proceso de taller',
        help='Proceso que transforma el producto base en este producto final. Ejemplo: Pulido.',
    )
    stone_workshop_operation_mode = fields.Selection(
        WORKSHOP_OPERATION_MODE_SELECTION,
        string='Modo operativo de taller',
        default='slab_finish',
    )
    stone_workshop_trigger = fields.Selection(
        WORKSHOP_TRIGGER_SELECTION,
        string='Disparador de taller',
        default='on_shortage',
        required=True,
        help=(
            'Siempre: crea orden de taller al confirmar la venta. '\
            'Solo si falta producto final: crea taller cuando no hay stock final suficiente. '\
            'Manual: no crea taller automáticamente.'
        ),
    )
    stone_workshop_auto_create = fields.Boolean(
        string='Crear OT automáticamente',
        default=True,
        help='Si está activo, la orden de venta puede generar la OT vinculada al confirmarse.',
    )
    stone_workshop_commercial_mode = fields.Selection(
        WORKSHOP_COMMERCIAL_MODE_SELECTION,
        string='Modo comercial',
        default='single_line',
        required=True,
        help=(
            'Una línea: el servicio queda integrado en el precio del producto final. '\
            'Producto + servicio visible: se puede agregar una línea comercial de servicio aparte.'
        ),
    )
    stone_workshop_service_product_id = fields.Many2one(
        'product.product',
        string='Servicio de taller sugerido',
        domain=[('type', '=', 'service')],
        help='Servicio comercial opcional para desglose visible. No controla inventario.',
    )


class ProductProduct(models.Model):
    _inherit = 'product.product'

    stone_workshop_required = fields.Boolean(
        related='product_tmpl_id.stone_workshop_required',
        readonly=False,
    )
    stone_workshop_base_product_id = fields.Many2one(
        related='product_tmpl_id.stone_workshop_base_product_id',
        readonly=False,
    )
    stone_workshop_process_id = fields.Many2one(
        related='product_tmpl_id.stone_workshop_process_id',
        readonly=False,
    )
    stone_workshop_operation_mode = fields.Selection(
        related='product_tmpl_id.stone_workshop_operation_mode',
        readonly=False,
    )
    stone_workshop_trigger = fields.Selection(
        related='product_tmpl_id.stone_workshop_trigger',
        readonly=False,
    )
    stone_workshop_auto_create = fields.Boolean(
        related='product_tmpl_id.stone_workshop_auto_create',
        readonly=False,
    )
    stone_workshop_commercial_mode = fields.Selection(
        related='product_tmpl_id.stone_workshop_commercial_mode',
        readonly=False,
    )
    stone_workshop_service_product_id = fields.Many2one(
        related='product_tmpl_id.stone_workshop_service_product_id',
        readonly=False,
    )
