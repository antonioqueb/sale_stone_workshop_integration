# -*- coding: utf-8 -*-

from odoo import fields, models


WORKSHOP_OPERATION_MODE_SELECTION = [
    ('slab_finish', 'Acabado de placa'),
    ('cut_to_size', 'Corte a medida'),
    ('edge_finish', 'Perfilado / acabado de canto'),
    ('custom_process', 'Proceso personalizado'),
]

WORKSHOP_TRIGGER_SELECTION = [
    ('on_shortage', 'Solo si falta inventario final'),
    ('always', 'Siempre crear OT'),
    ('manual', 'Manual'),
]

WORKSHOP_COMMERCIAL_MODE_SELECTION = [
    ('single_line', 'Una sola línea comercial'),
    ('service_line', 'Agregar servicio de taller'),
]


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    stone_workshop_required = fields.Boolean(
        string='Requiere taller',
        copy=True,
        help='Indica que este producto se vende como producto final que debe producirse o transformarse en taller.',
    )

    stone_workshop_auto_create = fields.Boolean(
        string='Crear OT automática',
        default=True,
        copy=True,
        help='Si está activo, la orden de taller puede generarse automáticamente al confirmar la venta.',
    )

    stone_workshop_base_product_id = fields.Many2one(
        'product.product',
        string='Producto base',
        domain=[('tracking', '!=', 'none')],
        copy=True,
        help='Producto base que será apartado o consumido para producir el producto final vendido.',
    )

    stone_workshop_process_id = fields.Many2one(
        'workshop.process',
        string='Proceso taller',
        copy=True,
        help='Proceso de taller sugerido para transformar el producto base en el producto final.',
    )

    stone_workshop_operation_mode = fields.Selection(
        WORKSHOP_OPERATION_MODE_SELECTION,
        string='Modo taller',
        default='slab_finish',
        copy=True,
        help='Informativo/comercial. El modo operativo real de la orden de taller '
             'lo dicta siempre el proceso seleccionado (su "modo operativo sugerido").',
    )

    stone_workshop_trigger = fields.Selection(
        WORKSHOP_TRIGGER_SELECTION,
        string='Disparador taller',
        default='on_shortage',
        copy=True,
    )

    stone_workshop_commercial_mode = fields.Selection(
        WORKSHOP_COMMERCIAL_MODE_SELECTION,
        string='Modo comercial taller',
        default='single_line',
        copy=True,
    )

    stone_workshop_service_product_id = fields.Many2one(
        'product.product',
        string='Servicio taller sugerido',
        domain=[('type', '=', 'service')],
        copy=True,
        help='Servicio comercial sugerido si se decide separar el producto final del servicio de taller.',
    )


class ProductProduct(models.Model):
    _inherit = 'product.product'

    stone_workshop_required = fields.Boolean(
        related='product_tmpl_id.stone_workshop_required',
        readonly=False,
        store=True,
    )

    stone_workshop_auto_create = fields.Boolean(
        related='product_tmpl_id.stone_workshop_auto_create',
        readonly=False,
        store=True,
    )

    stone_workshop_base_product_id = fields.Many2one(
        related='product_tmpl_id.stone_workshop_base_product_id',
        readonly=False,
        store=True,
    )

    stone_workshop_process_id = fields.Many2one(
        related='product_tmpl_id.stone_workshop_process_id',
        readonly=False,
        store=True,
    )

    stone_workshop_operation_mode = fields.Selection(
        related='product_tmpl_id.stone_workshop_operation_mode',
        readonly=False,
        store=True,
    )

    stone_workshop_trigger = fields.Selection(
        related='product_tmpl_id.stone_workshop_trigger',
        readonly=False,
        store=True,
    )

    stone_workshop_commercial_mode = fields.Selection(
        related='product_tmpl_id.stone_workshop_commercial_mode',
        readonly=False,
        store=True,
    )

    stone_workshop_service_product_id = fields.Many2one(
        related='product_tmpl_id.stone_workshop_service_product_id',
        readonly=False,
        store=True,
    )