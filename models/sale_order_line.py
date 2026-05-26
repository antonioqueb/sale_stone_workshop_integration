# -*- coding: utf-8 -*-
import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

from .product import WORKSHOP_OPERATION_MODE_SELECTION, WORKSHOP_TRIGGER_SELECTION, WORKSHOP_COMMERCIAL_MODE_SELECTION

_logger = logging.getLogger(__name__)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    stone_workshop_required = fields.Boolean(
        string='Requiere taller',
        copy=True,
        help='Indica que esta línea vende un producto final que debe producirse o transformarse en taller.',
    )
    stone_workshop_auto_create = fields.Boolean(
        string='Crear OT automática',
        default=True,
        copy=True,
    )
    stone_workshop_base_product_id = fields.Many2one(
        'product.product',
        string='Producto base',
        domain=[('tracking', '!=', 'none')],
        copy=True,
        help='Producto que se aparta/consume para fabricar el producto vendido en esta línea.',
    )
    stone_workshop_process_id = fields.Many2one(
        'workshop.process',
        string='Proceso taller',
        copy=True,
    )
    stone_workshop_operation_mode = fields.Selection(
        WORKSHOP_OPERATION_MODE_SELECTION,
        string='Modo taller',
        default='slab_finish',
        copy=True,
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
    )
    stone_workshop_order_id = fields.Many2one(
        'workshop.order',
        string='Orden de taller',
        copy=False,
        readonly=True,
        ondelete='set null',
    )
    stone_workshop_state = fields.Selection(
        related='stone_workshop_order_id.state',
        string='Estado taller',
        readonly=True,
        store=True,
    )
    stone_workshop_assignment_state = fields.Selection([
        ('none', 'Sin taller'),
        ('pending_inputs', 'Pendiente placas base'),
        ('reserved_inputs', 'Placas base reservadas'),
        ('in_workshop', 'En taller'),
        ('outputs_pending', 'Salida pendiente'),
        ('assigned', 'Producto final asignado'),
        ('cancelled', 'Cancelado'),
    ], string='Asignación taller', compute='_compute_stone_workshop_status', store=True)
    stone_workshop_input_lot_ids = fields.Many2many(
        'stock.lot',
        'sale_line_workshop_input_lot_rel',
        'sale_line_id',
        'lot_id',
        string='Lotes base taller',
        compute='_compute_stone_workshop_lots',
        store=False,
    )
    stone_workshop_output_lot_ids = fields.Many2many(
        'stock.lot',
        'sale_line_workshop_output_lot_rel',
        'sale_line_id',
        'lot_id',
        string='Lotes finales taller',
        compute='_compute_stone_workshop_lots',
        store=False,
    )
    stone_workshop_input_count = fields.Integer(
        string='Placas base',
        compute='_compute_stone_workshop_lots',
        store=False,
    )
    stone_workshop_output_count = fields.Integer(
        string='Lotes finales',
        compute='_compute_stone_workshop_lots',
        store=False,
    )
    stone_is_workshop_service_line = fields.Boolean(
        string='Línea de servicio taller',
        copy=False,
        help='Marca técnica para una línea de servicio comercial relacionada con taller.',
    )
    stone_workshop_parent_line_id = fields.Many2one(
        'sale.order.line',
        string='Línea producto taller',
        copy=False,
        ondelete='set null',
    )
    stone_workshop_hide_from_customer = fields.Boolean(
        string='Ocultar al cliente',
        copy=False,
        help='Campo técnico para reportes personalizados. El integrador no altera reportes fiscales estándar.',
    )

    @api.depends(
        'stone_workshop_required',
        'stone_workshop_order_id.state',
        'stone_workshop_order_id.input_line_ids.state',
        'stone_workshop_order_id.output_line_ids.state',
        'stone_workshop_order_id.output_line_ids.lot_id',
        'lot_ids',
    )
    def _compute_stone_workshop_status(self):
        for line in self:
            order = line.stone_workshop_order_id
            if not line.stone_workshop_required:
                line.stone_workshop_assignment_state = 'none'
                continue
            if not order:
                line.stone_workshop_assignment_state = 'pending_inputs'
                continue
            if order.state == 'cancel':
                line.stone_workshop_assignment_state = 'cancelled'
                continue

            input_lines = order.input_line_ids.filtered(lambda l: l.state != 'cancelled')
            output_lines = order.output_line_ids.filtered(lambda l: l.state != 'cancelled')
            final_output_lots = output_lines.filtered(
                lambda o: o.output_type not in ('scrap', 'rejected')
                and o.product_id == line.product_id
                and o.lot_id
            ).mapped('lot_id')

            if final_output_lots and set(final_output_lots.ids).issubset(set(line.lot_ids.ids)):
                line.stone_workshop_assignment_state = 'assigned'
            elif order.state in ('sent_to_workshop', 'in_progress', 'partial_done'):
                line.stone_workshop_assignment_state = 'in_workshop'
            elif output_lines:
                line.stone_workshop_assignment_state = 'outputs_pending'
            elif input_lines.filtered(lambda l: l.state == 'reserved_for_workshop'):
                line.stone_workshop_assignment_state = 'reserved_inputs'
            else:
                line.stone_workshop_assignment_state = 'pending_inputs'

    @api.depends(
        'stone_workshop_order_id.input_line_ids.lot_id',
        'stone_workshop_order_id.output_line_ids.lot_id',
        'stone_workshop_order_id.input_line_ids.state',
        'stone_workshop_order_id.output_line_ids.state',
    )
    def _compute_stone_workshop_lots(self):
        for line in self:
            order = line.stone_workshop_order_id
            input_lots = self.env['stock.lot']
            output_lots = self.env['stock.lot']

            if order:
                input_lots = order.input_line_ids.filtered(
                    lambda l: l.state != 'cancelled' and l.lot_id
                ).mapped('lot_id')
                output_lots = order.output_line_ids.filtered(
                    lambda l: l.state != 'cancelled'
                    and l.output_type not in ('scrap', 'rejected')
                    and l.lot_id
                ).mapped('lot_id')

            line.stone_workshop_input_lot_ids = input_lots
            line.stone_workshop_output_lot_ids = output_lots
            line.stone_workshop_input_count = len(input_lots)
            line.stone_workshop_output_count = len(output_lots)

    @api.model
    def _stone_workshop_vals_from_product(self, product):
        if not product or not product.exists() or not product.stone_workshop_required:
            return {
                'stone_workshop_required': False,
                'stone_workshop_base_product_id': False,
                'stone_workshop_process_id': False,
                'stone_workshop_operation_mode': 'slab_finish',
                'stone_workshop_trigger': 'on_shortage',
                'stone_workshop_auto_create': True,
                'stone_workshop_commercial_mode': 'single_line',
                'stone_workshop_service_product_id': False,
            }

        return {
            'stone_workshop_required': True,
            'stone_workshop_base_product_id': product.stone_workshop_base_product_id.id or False,
            'stone_workshop_process_id': product.stone_workshop_process_id.id or False,
            'stone_workshop_operation_mode': product.stone_workshop_operation_mode or 'slab_finish',
            'stone_workshop_trigger': product.stone_workshop_trigger or 'on_shortage',
            'stone_workshop_auto_create': product.stone_workshop_auto_create,
            'stone_workshop_commercial_mode': product.stone_workshop_commercial_mode or 'single_line',
            'stone_workshop_service_product_id': product.stone_workshop_service_product_id.id or False,
        }

    @api.onchange('product_id')
    def _onchange_stone_workshop_product_id(self):
        for line in self:
            if line.display_type or line.stone_is_workshop_service_line:
                continue

            vals = line._stone_workshop_vals_from_product(line.product_id)
            for key, value in vals.items():
                line[key] = value

    @api.model
    def _stone_workshop_prepare_create_vals(self, vals):
        clean = dict(vals or {})
        product_id = clean.get('product_id')

        if not product_id or clean.get('display_type') or clean.get('stone_is_workshop_service_line'):
            return clean

        try:
            product = self.env['product.product'].browse(int(product_id)).exists()
        except (TypeError, ValueError):
            product = self.env['product.product']

        if not product:
            return clean

        product_vals = self._stone_workshop_vals_from_product(product)

        for key, value in product_vals.items():
            clean.setdefault(key, value)

        return clean

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [self._stone_workshop_prepare_create_vals(vals) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals or {})

        if (
            'product_id' in vals
            and not self.env.context.get('skip_stone_workshop_product_defaults')
            and len(self) == 1
            and not self.display_type
            and not self.stone_is_workshop_service_line
        ):
            try:
                product = self.env['product.product'].browse(int(vals.get('product_id'))).exists()
            except (TypeError, ValueError):
                product = self.env['product.product']

            if product:
                defaults = self._stone_workshop_vals_from_product(product)
                for key, value in defaults.items():
                    vals.setdefault(key, value)

        return super().write(vals)

    def _stone_workshop_line_qty_done_or_reserved(self):
        self.ensure_one()
        qty = 0.0

        for move in self.move_ids.filtered(lambda m: m.state not in ('cancel',)):
            for ml in move.move_line_ids:
                if ml.product_id != self.product_id:
                    continue

                if 'quantity' in ml._fields:
                    qty += ml.quantity or 0.0
                elif 'reserved_uom_qty' in ml._fields:
                    qty += ml.reserved_uom_qty or 0.0
                elif 'qty_done' in ml._fields:
                    qty += ml.qty_done or 0.0

        return qty

    def _stone_workshop_final_available_qty(self):
        self.ensure_one()

        if not self.product_id:
            return 0.0

        domain = [
            ('product_id', '=', self.product_id.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]

        warehouse = self.order_id.warehouse_id if self.order_id else False
        if warehouse and warehouse.lot_stock_id:
            domain.append(('location_id', 'child_of', warehouse.lot_stock_id.id))

        qty = 0.0
        for quant in self.env['stock.quant'].search(domain):
            reserved = quant.reserved_quantity if 'reserved_quantity' in quant._fields else 0.0
            qty += (quant.quantity or 0.0) - (reserved or 0.0)

        return max(qty, 0.0)

    def _stone_workshop_get_line_uom(self):
        """Obtiene la unidad de medida de la línea de forma compatible con Odoo 19.

        En Odoo 19 Enterprise el campo de la línea puede estar expuesto como
        product_uom_id, mientras que en versiones anteriores era product_uom.
        No se debe acceder directamente a self.product_uom porque puede no existir.
        """
        self.ensure_one()

        for field_name in ('product_uom_id', 'product_uom'):
            if field_name in self._fields:
                uom = self[field_name]
                if uom:
                    return uom

        if self.product_id and self.product_id.uom_id:
            return self.product_id.uom_id

        return self.env['uom.uom']

    def _stone_workshop_needs_supply(self):
        self.ensure_one()

        if not self.stone_workshop_required:
            return False
        if self.stone_workshop_order_id:
            return False
        if not self.stone_workshop_auto_create:
            return False
        if self.display_type or self.stone_is_workshop_service_line:
            return False
        if not self.product_id or self.product_id.type == 'service':
            return False
        if not self.stone_workshop_base_product_id or not self.stone_workshop_process_id:
            return False

        trigger = self.stone_workshop_trigger or 'on_shortage'

        if trigger == 'manual':
            return False
        if trigger == 'always':
            return True

        line_uom = self._stone_workshop_get_line_uom()
        rounding = (
            (line_uom.rounding if line_uom else 0.0)
            or (self.product_id.uom_id.rounding if self.product_id and self.product_id.uom_id else 0.0)
            or 0.00001
        )

        required = self.product_uom_qty or 0.0
        reserved_on_sale = self._stone_workshop_line_qty_done_or_reserved()
        free_final = self._stone_workshop_final_available_qty()
        covered = reserved_on_sale + free_final

        return float_compare(covered, required, precision_rounding=rounding) < 0

    def action_view_stone_workshop_order(self):
        self.ensure_one()

        if not self.stone_workshop_order_id:
            raise UserError(_('Esta línea no tiene orden de taller vinculada.'))

        return {
            'type': 'ir.actions.act_window',
            'name': _('Orden de Taller'),
            'res_model': 'workshop.order',
            'view_mode': 'form',
            'res_id': self.stone_workshop_order_id.id,
            'target': 'current',
        }

    def action_create_stone_workshop_order(self):
        created = self.order_id._stone_workshop_create_missing_orders(force_lines=self)

        if len(created) == 1:
            return {
                'type': 'ir.actions.act_window',
                'name': _('Orden de Taller'),
                'res_model': 'workshop.order',
                'view_mode': 'form',
                'res_id': created.id,
                'target': 'current',
            }

        return True

    def _stone_workshop_parse_breakdown(self):
        self.ensure_one()

        raw = getattr(self, 'x_lot_breakdown_json', False)

        if not raw:
            return {}

        if isinstance(raw, dict):
            return dict(raw)

        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}

        return {}