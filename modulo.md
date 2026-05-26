## ./__manifest__.py
```py
# -*- coding: utf-8 -*-
{
    'name': 'Sale Stone Workshop Integration',
    'version': '19.0.1.1.0',
    'category': 'Sales/Manufacturing',
    'summary': 'Integra venta, selección de placas, taller y entregas para transformar producto base en producto final',
    'description': """
Integración operativa para piedra natural:
- Venta siempre sobre el producto final prometido al cliente.
- Orden de taller vinculada a la línea de venta cuando el producto final requiere proceso.
- Selección visual independiente de placas base/insumos desde la venta.
- Reserva de placas base para taller sin asignarlas como lotes de la línea final.
- Consumo de producto base y producción de producto final desde Stone Workshop.
- Asignación automática de lotes finales al pedido de origen al recibir salidas de taller.
- Exclusión de placas reservadas en taller y selecciones base desde el selector visual de venta.
    """,
    'author': 'Alphaqueb Consulting SAS',
    'website': 'https://alphaqueb.com',
    'license': 'LGPL-3',
    'depends': [
        'sale_management',
        'sale_stock',
        'stock',
        'product',
        'sale_stone_selection',
        'stone_workshop',
        'sale_delivery_wizard',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/product_views.xml',
        'views/sale_order_views.xml',
        'views/workshop_order_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sale_stone_workshop_integration/static/src/scss/workshop_input_selector.scss',
            'sale_stone_workshop_integration/static/src/components/workshop_input_selector/workshop_input_selector.xml',
            'sale_stone_workshop_integration/static/src/components/workshop_input_selector/workshop_input_selector.js',
        ],
    },
    'installable': True,
    'application': False,
}
```

## ./models/__init__.py
```py
# -*- coding: utf-8 -*-

from . import product
from . import sale_workshop_input_selection
from . import sale_order_line
from . import sale_order
from . import workshop_order
from . import stock_quant```

## ./models/sale_order_line.py
```py
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

    stone_workshop_input_selection_ids = fields.One2many(
        'sale.stone.workshop.input.selection',
        'sale_line_id',
        string='Placas base a consumir',
        copy=False,
    )
    stone_workshop_input_selector_anchor = fields.Boolean(
        string='Selector placas base',
        compute='_compute_stone_workshop_input_selection_summary',
    )
    stone_workshop_input_selection_count = fields.Integer(
        string='Placas base seleccionadas',
        compute='_compute_stone_workshop_input_selection_summary',
    )
    stone_workshop_input_selection_total_qty = fields.Float(
        string='Total base seleccionado',
        compute='_compute_stone_workshop_input_selection_summary',
        digits=(12, 4),
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
        'stone_workshop_input_selection_ids.state',
        'lot_ids',
    )
    def _compute_stone_workshop_status(self):
        for line in self:
            order = line.stone_workshop_order_id
            if not line.stone_workshop_required:
                line.stone_workshop_assignment_state = 'none'
                continue

            active_selections = line.stone_workshop_input_selection_ids.filtered(
                lambda s: s.state != 'cancelled'
            )

            if not order:
                line.stone_workshop_assignment_state = 'reserved_inputs' if active_selections else 'pending_inputs'
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
            elif input_lines.filtered(lambda l: l.state == 'reserved_for_workshop') or active_selections:
                line.stone_workshop_assignment_state = 'reserved_inputs'
            else:
                line.stone_workshop_assignment_state = 'pending_inputs'

    @api.depends(
        'stone_workshop_order_id.input_line_ids.lot_id',
        'stone_workshop_order_id.output_line_ids.lot_id',
        'stone_workshop_order_id.input_line_ids.state',
        'stone_workshop_order_id.output_line_ids.state',
        'stone_workshop_input_selection_ids.lot_id',
        'stone_workshop_input_selection_ids.state',
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

            if not input_lots:
                input_lots = line.stone_workshop_input_selection_ids.filtered(
                    lambda s: s.state != 'cancelled' and s.lot_id
                ).mapped('lot_id')

            line.stone_workshop_input_lot_ids = input_lots
            line.stone_workshop_output_lot_ids = output_lots
            line.stone_workshop_input_count = len(input_lots)
            line.stone_workshop_output_count = len(output_lots)

    @api.depends(
        'stone_workshop_required',
        'stone_workshop_base_product_id',
        'stone_workshop_input_selection_ids.qty_in',
        'stone_workshop_input_selection_ids.state',
    )
    def _compute_stone_workshop_input_selection_summary(self):
        for line in self:
            selections = line.stone_workshop_input_selection_ids.filtered(
                lambda s: s.state != 'cancelled'
            )
            line.stone_workshop_input_selector_anchor = bool(
                line.stone_workshop_required and line.stone_workshop_base_product_id
            )
            line.stone_workshop_input_selection_count = len(selections)
            line.stone_workshop_input_selection_total_qty = sum(selections.mapped('qty_in'))

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
        """Obtiene la unidad de medida de la línea de forma compatible con Odoo 19."""
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

    # -------------------------------------------------------------------------
    # Selección de placas base para taller desde la venta
    # -------------------------------------------------------------------------

    def _stone_workshop_active_input_selections(self):
        return self.stone_workshop_input_selection_ids.filtered(
            lambda s: s.state != 'cancelled'
        )

    def _stone_workshop_assert_can_select_base_inputs(self):
        self.ensure_one()

        if self.display_type or self.stone_is_workshop_service_line:
            raise UserError(_('Esta línea no permite selección de placas base.'))
        if self.order_id.state not in ('sale', 'done'):
            raise UserError(_(
                'La selección de placas base para taller solo está permitida en órdenes de venta confirmadas.'
            ))
        if not self.stone_workshop_required:
            raise UserError(_('La línea no está marcada como Requiere taller.'))
        if not self.stone_workshop_base_product_id:
            raise UserError(_('Configura el producto base antes de seleccionar placas.'))
        if not self.stone_workshop_process_id:
            raise UserError(_('Configura el proceso de taller antes de seleccionar placas.'))

    def _stone_workshop_safe_int_list(self, values):
        result = []
        for value in values or []:
            try:
                clean = int(value)
            except (TypeError, ValueError):
                continue
            if clean and clean not in result:
                result.append(clean)
        return result

    def _stone_workshop_parse_qty_breakdown(self, breakdown):
        result = {}
        if not breakdown:
            return result
        if isinstance(breakdown, str):
            try:
                breakdown = json.loads(breakdown)
            except (json.JSONDecodeError, TypeError):
                breakdown = {}
        if not isinstance(breakdown, dict):
            return result
        for key, value in breakdown.items():
            try:
                lot_id = int(key)
                qty = float(value or 0.0)
            except (TypeError, ValueError):
                continue
            if lot_id and qty > 0:
                result[lot_id] = qty
        return result

    def _stone_workshop_prepare_selection_vals_from_lots(self, lot_ids, breakdown=None):
        self.ensure_one()

        safe_lot_ids = self._stone_workshop_safe_int_list(lot_ids)
        if not safe_lot_ids:
            return []

        breakdown = self._stone_workshop_parse_qty_breakdown(breakdown)
        warehouse = self.order_id.warehouse_id
        location_id = warehouse.lot_stock_id.id if warehouse and warehouse.lot_stock_id else False

        vals_list = self.env['workshop.order'].prepare_input_line_vals_from_lots(
            self.stone_workshop_base_product_id.id,
            safe_lot_ids,
            location_id=location_id,
        )

        for vals in vals_list:
            lot_id = vals.get('lot_id')
            if isinstance(lot_id, (list, tuple)):
                lot_id = lot_id[0] if lot_id else False
            try:
                lot_id = int(lot_id or 0)
            except (TypeError, ValueError):
                lot_id = 0

            # prepare_input_line_vals_from_lots() devuelve valores para
            # workshop.input.line. La selección en venta usa base_product_id
            # para evitar colisión semántica con el producto final vendido.
            vals.pop('product_id', None)

            manual_qty = breakdown.get(lot_id)
            if manual_qty and manual_qty > 0:
                vals['qty_in'] = manual_qty
                vals['area_sqm'] = manual_qty

            vals.update({
                'sale_order_id': self.order_id.id,
                'sale_line_id': self.id,
                'product_final_id': self.product_id.id,
                'base_product_id': self.stone_workshop_base_product_id.id,
                'reserved_origin': '%s / %s' % (
                    self.order_id.name or '',
                    self.product_id.display_name or '',
                ),
                'state': 'selected',
            })

        return vals_list

    def _stone_workshop_validate_lots_not_committed_elsewhere(self, target_lot_ids):
        self.ensure_one()

        target_lot_ids = set(target_lot_ids or [])
        if not target_lot_ids:
            return True

        current_lot_ids = set(self._stone_workshop_active_input_selections().mapped('lot_id').ids)
        committed_lot_ids = set(self.env['stock.quant']._get_committed_lot_ids(
            self.stone_workshop_base_product_id.id
        ))
        conflict_ids = target_lot_ids & (committed_lot_ids - current_lot_ids)

        if conflict_ids:
            lots = self.env['stock.lot'].browse(list(conflict_ids))
            raise UserError(_(
                'No puedes seleccionar estas placas porque ya están comprometidas en otra venta, apartado u orden de taller:\n%s'
            ) % ', '.join(lots.mapped('name')))

        return True

    def get_workshop_input_selector_data(self):
        self.ensure_one()

        selections = self._stone_workshop_active_input_selections()
        selected_lot_ids = selections.mapped('lot_id').ids
        breakdown = {
            str(selection.lot_id.id): selection.qty_in
            for selection in selections
            if selection.lot_id and selection.qty_in
        }

        return {
            'line_id': self.id,
            'sale_order_id': self.order_id.id,
            'sale_order_name': self.order_id.name or '',
            'state': self.order_id.state,
            'can_select': bool(
                self.order_id.state in ('sale', 'done')
                and self.stone_workshop_required
                and self.stone_workshop_base_product_id
                and self.stone_workshop_process_id
            ),
            'workshop_required': bool(self.stone_workshop_required),
            'has_workshop_order': bool(self.stone_workshop_order_id),
            'workshop_order_id': self.stone_workshop_order_id.id if self.stone_workshop_order_id else False,
            'workshop_order_name': self.stone_workshop_order_id.name if self.stone_workshop_order_id else '',
            'product_final_id': self.product_id.id if self.product_id else False,
            'product_final_name': self.product_id.display_name if self.product_id else '',
            'base_product_id': self.stone_workshop_base_product_id.id if self.stone_workshop_base_product_id else False,
            'base_product_name': self.stone_workshop_base_product_id.display_name if self.stone_workshop_base_product_id else '',
            'process_id': self.stone_workshop_process_id.id if self.stone_workshop_process_id else False,
            'process_name': self.stone_workshop_process_id.display_name if self.stone_workshop_process_id else '',
            'requested_qty': self.product_uom_qty or 0.0,
            'selected_lot_ids': selected_lot_ids,
            'breakdown': breakdown,
            'selected_count': len(selections),
            'selected_qty': sum(selections.mapped('qty_in')),
            'selections': [{
                'id': selection.id,
                'lot_id': selection.lot_id.id,
                'lot_name': selection.lot_id.name or '',
                'base_product_id': selection.base_product_id.id,
                'base_product_name': selection.base_product_id.display_name or '',
                'qty_in': selection.qty_in or 0.0,
                'area_sqm': selection.area_sqm or 0.0,
                'material_type': selection.material_type or '',
                'state': selection.state or '',
                'state_label': dict(selection._fields['state'].selection).get(selection.state, selection.state),
                'location_id': selection.location_id.id if selection.location_id else False,
                'location_name': selection.location_id.display_name if selection.location_id else '',
                'workshop_order_id': selection.workshop_order_id.id if selection.workshop_order_id else False,
                'workshop_order_name': selection.workshop_order_id.name if selection.workshop_order_id else '',
            } for selection in selections],
        }

    def write_workshop_input_selection_from_lots(self, lot_ids=None, breakdown=None):
        self.ensure_one()
        self._stone_workshop_assert_can_select_base_inputs()

        safe_lot_ids = self._stone_workshop_safe_int_list(lot_ids)
        self._stone_workshop_validate_lots_not_committed_elsewhere(safe_lot_ids)

        Selection = self.env['sale.stone.workshop.input.selection']
        active_selections = self._stone_workshop_active_input_selections()

        locked = active_selections.filtered(
            lambda s: s.workshop_input_line_id and s.workshop_input_line_id.is_consumed
        )
        if locked:
            raise UserError(_(
                'No puedes modificar la selección porque ya hay placas enviadas/consumidas en taller: %s'
            ) % ', '.join(locked.mapped('lot_id.name')))

        vals_list = self._stone_workshop_prepare_selection_vals_from_lots(
            safe_lot_ids,
            breakdown=breakdown,
        )
        vals_by_lot = {}
        for vals in vals_list:
            lot_id = vals.get('lot_id')
            if isinstance(lot_id, (list, tuple)):
                lot_id = lot_id[0] if lot_id else False
            if lot_id:
                vals_by_lot[int(lot_id)] = vals

        to_cancel = active_selections.filtered(lambda s: s.lot_id.id not in safe_lot_ids)
        for selection in to_cancel:
            input_line = selection.workshop_input_line_id
            if input_line and input_line.exists() and not input_line.is_consumed:
                input_line.unlink()
            selection.write({
                'state': 'cancelled',
                'workshop_input_line_id': False,
                'workshop_order_id': False,
            })

        for lot_id in safe_lot_ids:
            vals = vals_by_lot.get(lot_id)
            if not vals:
                continue

            selection = active_selections.filtered(lambda s: s.lot_id.id == lot_id)[:1]
            if selection:
                allowed_vals = dict(vals)
                allowed_vals.pop('sale_order_id', None)
                allowed_vals.pop('sale_line_id', None)
                allowed_vals.pop('product_final_id', None)
                selection.write(allowed_vals)
            else:
                Selection.create(vals)

        if self.stone_workshop_order_id:
            self._stone_workshop_push_input_selections_to_workshop(self.stone_workshop_order_id)

        return self.get_workshop_input_selector_data()

    def _stone_workshop_push_input_selections_to_workshop(self, workshop):
        self.ensure_one()

        if not workshop:
            return False

        active_selections = self._stone_workshop_active_input_selections()
        if not active_selections:
            return False

        if workshop.state in ('sent_to_workshop', 'in_progress', 'partial_done', 'done', 'cancel'):
            consumed = active_selections.filtered(
                lambda s: s.workshop_input_line_id and s.workshop_input_line_id.is_consumed
            )
            if consumed:
                return True

        WorkshopInput = self.env['workshop.input.line']
        created_or_updated = WorkshopInput

        for selection in active_selections:
            vals = selection._to_workshop_input_vals(workshop)
            input_line = selection.workshop_input_line_id

            if input_line and input_line.exists():
                if input_line.is_consumed:
                    continue
                input_line.with_context(skip_sale_workshop_reservation=True).write(vals)
            else:
                input_line = WorkshopInput.with_context(skip_sale_workshop_reservation=True).create(vals)

            selection.write({
                'workshop_order_id': workshop.id,
                'workshop_input_line_id': input_line.id,
            })
            created_or_updated |= input_line

        if created_or_updated and hasattr(workshop, '_sale_workshop_refresh_input_reservation'):
            workshop._sale_workshop_refresh_input_reservation()

        active_selections._sync_state_from_workshop_input()
        return True

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
```

## ./models/sale_order.py
```py
# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    stone_workshop_order_ids = fields.One2many(
        'workshop.order',
        'sale_order_id',
        string='Órdenes de taller',
        readonly=True,
    )
    stone_workshop_order_count = fields.Integer(
        string='Órdenes de taller',
        compute='_compute_stone_workshop_order_count',
    )
    stone_workshop_pending_count = fields.Integer(
        string='Taller pendiente',
        compute='_compute_stone_workshop_order_count',
    )

    stone_workshop_input_selection_ids = fields.One2many(
        'sale.stone.workshop.input.selection',
        'sale_order_id',
        string='Placas base a consumir',
        readonly=True,
    )
    stone_workshop_input_selection_count = fields.Integer(
        string='Placas base seleccionadas',
        compute='_compute_stone_workshop_input_selection_summary',
    )
    stone_workshop_input_selection_total_qty = fields.Float(
        string='Total base seleccionado',
        compute='_compute_stone_workshop_input_selection_summary',
        digits=(12, 4),
    )

    @api.depends('stone_workshop_order_ids.state')
    def _compute_stone_workshop_order_count(self):
        for order in self:
            orders = order.stone_workshop_order_ids
            order.stone_workshop_order_count = len(orders)
            order.stone_workshop_pending_count = len(
                orders.filtered(lambda o: o.state not in ('done', 'cancel'))
            )

    @api.depends(
        'stone_workshop_input_selection_ids.state',
        'stone_workshop_input_selection_ids.qty_in',
    )
    def _compute_stone_workshop_input_selection_summary(self):
        for order in self:
            selections = order.stone_workshop_input_selection_ids.filtered(
                lambda s: s.state != 'cancelled'
            )
            order.stone_workshop_input_selection_count = len(selections)
            order.stone_workshop_input_selection_total_qty = sum(selections.mapped('qty_in'))

    def action_confirm(self):
        res = super().action_confirm()

        confirmed_orders = self.env['sale.order']
        for order in self:
            is_backup = (
                'x_is_quote_backup' in order._fields
                and order.x_is_quote_backup
            )
            if order.state in ('sale', 'done') and not is_backup:
                confirmed_orders |= order

        if confirmed_orders:
            confirmed_orders._stone_workshop_create_missing_orders()

        return res

    # -------------------------------------------------------------------------
    # Preparación de valores
    # -------------------------------------------------------------------------

    def _stone_workshop_get_workshop_vals(self, line):
        self.ensure_one()

        warehouse = (
            self.warehouse_id
            or self.env['stock.warehouse'].search([
                ('company_id', '=', self.company_id.id)
            ], limit=1)
        )
        location_src = warehouse.lot_stock_id if warehouse else False

        notes = _(
            '<p><strong>Orden generada desde venta.</strong></p>'
            '<ul>'
            '<li>Pedido: %(sale)s</li>'
            '<li>Línea: %(line)s</li>'
            '<li>Producto vendido/final: %(final)s</li>'
            '<li>Producto base a apartar: %(base)s</li>'
            '<li>Proceso: %(process)s</li>'
            '</ul>'
        ) % {
            'sale': self.name or '',
            'line': line.name or line.product_id.display_name or '',
            'final': line.product_id.display_name or '',
            'base': line.stone_workshop_base_product_id.display_name or '',
            'process': line.stone_workshop_process_id.display_name or '',
        }

        vals = {
            'sale_order_id': self.id,
            'sale_line_id': line.id,
            'operation_mode': line.stone_workshop_operation_mode or 'slab_finish',
            'process_id': line.stone_workshop_process_id.id,
            'input_product_id': line.stone_workshop_base_product_id.id,
            'default_product_out_id': line.product_id.id,
            'production_target_sqm': line.product_uom_qty or 0.0,
            'target_pieces': 1,
            'warehouse_id': warehouse.id if warehouse else False,
            'location_src_id': location_src.id if location_src else False,
            'location_dest_id': location_src.id if location_src else False,
            'company_id': self.company_id.id,
            'date_planned': (
                self.commitment_date
                if 'commitment_date' in self._fields
                else False
            ),
            'notes': notes,
        }

        return vals

    # -------------------------------------------------------------------------
    # Diagnóstico de líneas
    # -------------------------------------------------------------------------

    def _stone_workshop_line_skip_reason(self, line, manual=False):
        self.ensure_one()

        if line.display_type:
            return _('es una sección/nota.')
        if getattr(line, 'stone_is_workshop_service_line', False):
            return _('es una línea de servicio de taller.')
        if not line.product_id:
            return _('no tiene producto.')
        if line.product_id.type == 'service':
            return _('el producto es de tipo servicio.')
        if not line.stone_workshop_required:
            return _('no está marcada como Requiere taller.')
        if line.stone_workshop_order_id:
            return _('ya tiene una orden de taller vinculada.')
        if not line.stone_workshop_base_product_id:
            return _('no tiene producto base configurado.')
        if not line.stone_workshop_process_id:
            return _('no tiene proceso de taller configurado.')

        if not manual:
            if not line.stone_workshop_auto_create:
                return _('tiene desactivada la creación automática de OT.')
            if line.stone_workshop_trigger == 'manual':
                return _('tiene disparador manual.')
            if not line._stone_workshop_needs_supply():
                return _('el disparador no aplica porque no se detectó faltante de producto final.')

        return False

    def _stone_workshop_manual_candidate_lines(self):
        """Líneas candidatas para el botón manual Crear OT taller."""
        SaleLine = self.env['sale.order.line']
        lines = SaleLine

        for order in self:
            for line in order.order_line:
                reason = order._stone_workshop_line_skip_reason(line, manual=True)
                if reason:
                    _logger.info(
                        '[STONE WORKSHOP SALE] Línea %s omitida en creación manual: %s',
                        line.id,
                        reason,
                    )
                    continue

                lines |= line

        return lines

    # -------------------------------------------------------------------------
    # Creación de órdenes de taller
    # -------------------------------------------------------------------------

    def _stone_workshop_create_missing_orders(self, force_lines=False):
        WorkshopOrder = self.env['workshop.order']
        created_orders = WorkshopOrder

        for order in self:
            if order.state not in ('sale', 'done'):
                _logger.info(
                    '[STONE WORKSHOP SALE] Orden %s omitida: state=%s',
                    order.name,
                    order.state,
                )
                continue

            if force_lines:
                candidate_lines = force_lines.filtered(lambda l: l.order_id == order)
                manual = True
            else:
                candidate_lines = order.order_line
                manual = False

            for line in candidate_lines:
                reason = order._stone_workshop_line_skip_reason(line, manual=manual)

                if reason:
                    _logger.info(
                        '[STONE WORKSHOP SALE] No se crea OT para línea %s (%s): %s',
                        line.id,
                        line.product_id.display_name if line.product_id else 'Sin producto',
                        reason,
                    )

                    if line.stone_workshop_order_id:
                        created_orders |= line.stone_workshop_order_id

                    continue

                if not manual and not line._stone_workshop_needs_supply():
                    _logger.info(
                        '[STONE WORKSHOP SALE] Línea %s no requiere abastecimiento según trigger.',
                        line.id,
                    )
                    continue

                vals = order._stone_workshop_get_workshop_vals(line)
                workshop = WorkshopOrder.create(vals)

                line.with_context(skip_stone_workshop_product_defaults=True).write({
                    'stone_workshop_order_id': workshop.id,
                })

                line._stone_workshop_push_input_selections_to_workshop(workshop)

                created_orders |= workshop

                body = _(
                    'Se creó la orden de taller '
                    '<a href="#" data-oe-model="workshop.order" data-oe-id="%(id)s">%(name)s</a> '
                    'para producir <strong>%(final)s</strong> desde <strong>%(base)s</strong>.'
                ) % {
                    'id': workshop.id,
                    'name': workshop.name,
                    'final': line.product_id.display_name,
                    'base': line.stone_workshop_base_product_id.display_name,
                }

                order.message_post(body=body)
                workshop.message_post(
                    body=_('Origen comercial: %s, línea %s.') % (
                        order.name,
                        line.display_name,
                    )
                )

                _logger.info(
                    '[STONE WORKSHOP SALE] Created workshop %s for sale %s line %s',
                    workshop.name,
                    order.name,
                    line.id,
                )

        return created_orders

    # -------------------------------------------------------------------------
    # Botones
    # -------------------------------------------------------------------------

    def action_create_stone_workshop_orders(self):
        for order in self:
            if order.state not in ('sale', 'done'):
                raise UserError(_(
                    'Solo puedes crear órdenes de taller desde una orden de venta confirmada.'
                ))

        candidate_lines = self._stone_workshop_manual_candidate_lines()

        if not candidate_lines:
            details = []

            for order in self:
                for line in order.order_line:
                    reason = order._stone_workshop_line_skip_reason(line, manual=True)
                    product_name = (
                        line.product_id.display_name
                        if line.product_id
                        else _('Sin producto')
                    )
                    details.append('- %s: %s' % (product_name, reason or _('apta')))

            raise UserError(_(
                'No se encontró ninguna línea apta para crear OT de taller.\n\n'
                'Revisa estas condiciones:\n'
                '- La orden debe estar confirmada.\n'
                '- La línea debe tener producto almacenable/consumible.\n'
                '- Requiere taller debe estar activo.\n'
                '- Debe tener producto base.\n'
                '- Debe tener proceso de taller.\n'
                '- No debe tener ya una OT vinculada.\n\n'
                'Diagnóstico:\n%s'
            ) % '\n'.join(details))

        created = self._stone_workshop_create_missing_orders(force_lines=candidate_lines)

        if not created:
            raise UserError(_(
                'No se creó ninguna orden de taller. '
                'Las líneas parecen aptas, pero no se generó registro. '
                'Revisa permisos de workshop.order o reglas de seguridad.'
            ))

        if len(self) == 1:
            return self.action_view_stone_workshop_orders()

        return True

    def action_sync_workshop_input_selections(self):
        for order in self:
            for line in order.order_line.filtered(lambda l: l.stone_workshop_order_id):
                line._stone_workshop_push_input_selections_to_workshop(line.stone_workshop_order_id)
        return True

    def action_view_stone_workshop_orders(self):
        self.ensure_one()

        action = {
            'type': 'ir.actions.act_window',
            'name': _('Órdenes de Taller'),
            'res_model': 'workshop.order',
            'view_mode': 'list,form',
            'domain': [('sale_order_id', '=', self.id)],
            'context': {
                'default_sale_order_id': self.id,
            },
        }

        if self.stone_workshop_order_count == 1:
            action.update({
                'view_mode': 'form',
                'res_id': self.stone_workshop_order_ids.id,
            })

        return action
```

## ./models/sale_workshop_input_selection.py
```py
# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)


WORKSHOP_INPUT_SELECTION_STATES = [
    ('selected', 'Seleccionada'),
    ('reserved', 'Reservada para taller'),
    ('moved_to_workshop', 'Movida a taller'),
    ('cancelled', 'Cancelada'),
]


MATERIAL_TYPE_SELECTION = [
    ('slab', 'Placa'),
    ('format', 'Formato'),
    ('pallet', 'Pallet'),
    ('remnant', 'Retazo'),
]


class SaleStoneWorkshopInputSelection(models.Model):
    _name = 'sale.stone.workshop.input.selection'
    _description = 'Selección de placas base para taller desde venta'
    _order = 'sale_order_id desc, sale_line_id, sequence, id'
    _rec_name = 'name'

    sequence = fields.Integer(default=10)

    sale_order_id = fields.Many2one(
        'sale.order',
        string='Orden de venta',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Línea de venta',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='sale_order_id.company_id',
        string='Compañía',
        readonly=True,
        store=True,
    )
    partner_id = fields.Many2one(
        related='sale_order_id.partner_id',
        string='Cliente',
        readonly=True,
        store=True,
    )

    product_final_id = fields.Many2one(
        'product.product',
        string='Producto final vendido',
        required=True,
        index=True,
        ondelete='restrict',
    )
    base_product_id = fields.Many2one(
        'product.product',
        string='Producto base a consumir',
        required=True,
        index=True,
        ondelete='restrict',
        domain=[('tracking', '!=', 'none')],
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string='Lote / placa base',
        required=True,
        index=True,
        ondelete='restrict',
        domain="[('product_id', '=', base_product_id)]",
    )
    quant_id = fields.Many2one(
        'stock.quant',
        string='Quant origen',
        ondelete='set null',
    )
    location_id = fields.Many2one(
        'stock.location',
        string='Ubicación origen',
        domain=[('usage', '=', 'internal')],
    )

    material_type = fields.Selection(
        MATERIAL_TYPE_SELECTION,
        string='Tipo material',
        default='slab',
        required=True,
    )
    qty_in = fields.Float(
        string='Cantidad a consumir',
        digits=(12, 4),
        default=1.0,
    )
    area_sqm = fields.Float(
        string='Área m²',
        digits=(12, 4),
    )
    width_cm = fields.Float(
        string='Ancho',
        digits=(12, 2),
    )
    height_cm = fields.Float(
        string='Alto',
        digits=(12, 2),
    )
    thickness_cm = fields.Float(
        string='Espesor',
        digits=(12, 2),
    )
    pieces = fields.Integer(
        string='Piezas',
        default=1,
    )
    block_name = fields.Char(string='Bloque')
    tone = fields.Char(string='Tono')
    current_finish = fields.Char(string='Acabado actual')
    reserved_origin = fields.Char(
        string='Compromiso comercial',
        compute='_compute_reserved_origin',
        store=True,
        readonly=False,
    )

    state = fields.Selection(
        WORKSHOP_INPUT_SELECTION_STATES,
        string='Estado',
        default='selected',
        required=True,
        index=True,
        tracking=True,
    )

    workshop_order_id = fields.Many2one(
        'workshop.order',
        string='Orden de taller',
        ondelete='set null',
        index=True,
    )
    workshop_input_line_id = fields.Many2one(
        'workshop.input.line',
        string='Entrada de taller',
        ondelete='set null',
        index=True,
    )
    consume_picking_id = fields.Many2one(
        related='workshop_input_line_id.consume_picking_id',
        string='Picking consumo',
        readonly=True,
        store=True,
    )

    available_qty = fields.Float(
        string='Disponible real',
        compute='_compute_available_qty',
        digits=(12, 4),
    )
    name = fields.Char(
        string='Descripción',
        compute='_compute_name',
        store=True,
    )

    @api.depends('sale_order_id.name', 'sale_line_id.product_id', 'base_product_id', 'lot_id')
    def _compute_reserved_origin(self):
        for line in self:
            if line.reserved_origin:
                continue
            parts = []
            if line.sale_order_id:
                parts.append(line.sale_order_id.name or '')
            if line.sale_line_id and line.sale_line_id.product_id:
                parts.append(line.sale_line_id.product_id.display_name or '')
            line.reserved_origin = ' / '.join([p for p in parts if p]) or False

    @api.depends('base_product_id', 'lot_id', 'qty_in')
    def _compute_name(self):
        for line in self:
            if line.lot_id:
                line.name = '%s / %s' % (
                    line.base_product_id.display_name or '',
                    line.lot_id.name or '',
                )
            else:
                line.name = line.base_product_id.display_name or _('Placa base')

    @api.depends('base_product_id', 'lot_id', 'location_id')
    def _compute_available_qty(self):
        for line in self:
            if not line.base_product_id or not line.lot_id:
                line.available_qty = 0.0
                continue

            domain = [
                ('product_id', '=', line.base_product_id.id),
                ('lot_id', '=', line.lot_id.id),
                ('location_id.usage', '=', 'internal'),
                ('quantity', '>', 0),
            ]
            if line.location_id:
                domain.append(('location_id', 'child_of', line.location_id.id))

            qty = 0.0
            for quant in self.env['stock.quant'].search(domain):
                reserved = quant.reserved_quantity if 'reserved_quantity' in quant._fields else 0.0
                qty += (quant.quantity or 0.0) - (reserved or 0.0)

            # Si esta misma selección ya creó una reserva, se considera disponible
            # para que la validación no se bloquee contra su propio compromiso.
            if line.workshop_order_id and line.workshop_input_line_id:
                qty += line.qty_in or 0.0

            line.available_qty = max(qty, 0.0)

    @api.constrains('sale_line_id', 'lot_id', 'state')
    def _check_unique_active_lot_per_sale_line(self):
        for rec in self.filtered(lambda r: r.state != 'cancelled' and r.sale_line_id and r.lot_id):
            domain = [
                ('id', '!=', rec.id),
                ('sale_line_id', '=', rec.sale_line_id.id),
                ('lot_id', '=', rec.lot_id.id),
                ('state', '!=', 'cancelled'),
            ]
            if self.search_count(domain):
                raise ValidationError(_(
                    'El lote %s ya está seleccionado para esta misma línea de venta.'
                ) % rec.lot_id.name)

    @api.constrains('base_product_id', 'lot_id')
    def _check_lot_matches_base_product(self):
        for rec in self.filtered(lambda r: r.base_product_id and r.lot_id):
            if rec.lot_id.product_id and rec.lot_id.product_id != rec.base_product_id:
                raise ValidationError(_(
                    'El lote %(lot)s pertenece al producto %(lot_product)s, no al producto base %(base_product)s.'
                ) % {
                    'lot': rec.lot_id.name,
                    'lot_product': rec.lot_id.product_id.display_name,
                    'base_product': rec.base_product_id.display_name,
                })

    @api.constrains('qty_in')
    def _check_positive_qty(self):
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure') or 4
        for rec in self.filtered(lambda r: r.state != 'cancelled'):
            if float_compare(rec.qty_in or 0.0, 0.0, precision_digits=precision) <= 0:
                raise ValidationError(_('La cantidad a consumir debe ser mayor a cero.'))

    @api.model_create_multi
    def create(self, vals_list):
        clean_vals_list = []
        for vals in vals_list:
            clean_vals_list.append(self._prepare_selection_values(dict(vals or {})))
        records = super().create(clean_vals_list)
        return records

    def write(self, vals):
        clean_vals = dict(vals or {})
        if clean_vals:
            clean_vals = self._prepare_selection_values(clean_vals, existing_records=self)
        return super().write(clean_vals)

    @api.model
    def _prepare_selection_values(self, vals, existing_records=False):
        for m2o_name in (
            'sale_order_id',
            'sale_line_id',
            'product_final_id',
            'base_product_id',
            'lot_id',
            'quant_id',
            'location_id',
            'workshop_order_id',
            'workshop_input_line_id',
            'consume_picking_id',
        ):
            raw_value = vals.get(m2o_name)
            if isinstance(raw_value, (list, tuple)):
                vals[m2o_name] = raw_value[0] if raw_value else False

        sale_line = False
        line_value = vals.get('sale_line_id')
        if line_value:
            sale_line = self.env['sale.order.line'].browse(int(line_value)).exists()
        elif existing_records and len(existing_records) == 1:
            sale_line = existing_records.sale_line_id

        if sale_line:
            vals.setdefault('sale_order_id', sale_line.order_id.id)
            vals.setdefault('product_final_id', sale_line.product_id.id)
            vals.setdefault('base_product_id', sale_line.stone_workshop_base_product_id.id)

        lot = False
        lot_value = vals.get('lot_id')
        if lot_value:
            lot = self.env['stock.lot'].browse(int(lot_value)).exists()

        if lot and not vals.get('base_product_id') and lot.product_id:
            vals['base_product_id'] = lot.product_id.id

        qty = self._safe_float(vals.get('qty_in'))
        area = self._safe_float(vals.get('area_sqm'))

        product = False
        product_id = vals.get('base_product_id')
        if product_id:
            product = self.env['product.product'].browse(int(product_id)).exists()

        if qty and (not area or self._product_uom_is_area(product)):
            vals['area_sqm'] = qty

        if not vals.get('reserved_origin') and sale_line:
            vals['reserved_origin'] = '%s / %s' % (
                sale_line.order_id.name or '',
                sale_line.product_id.display_name or '',
            )

        return vals

    @api.model
    def _safe_float(self, value, default=0.0):
        try:
            if value in (False, None, ''):
                return default
            if isinstance(value, str):
                value = value.replace(',', '.')
            return float(value)
        except (TypeError, ValueError):
            return default

    @api.model
    def _product_uom_is_area(self, product):
        if not product or not product.uom_id:
            return False
        uom = product.uom_id
        text = ' '.join(filter(None, [
            uom.name or '',
            uom.display_name or '',
        ])).lower()
        return any(token in text for token in (
            'm²',
            'm2',
            'm^2',
            'sqm',
            'sq m',
            'metro cuadrado',
            'metros cuadrados',
            'superficie',
            'area',
            'área',
        ))

    def _to_workshop_input_vals(self, workshop_order):
        self.ensure_one()
        return {
            'order_id': workshop_order.id,
            'material_type': self.material_type or 'slab',
            'product_id': self.base_product_id.id,
            'lot_id': self.lot_id.id,
            'qty_in': self.qty_in,
            'area_sqm': self.area_sqm or self.qty_in,
            'width_cm': self.width_cm,
            'height_cm': self.height_cm,
            'thickness_cm': self.thickness_cm,
            'pieces': self.pieces or 1,
            'block_name': self.block_name or '',
            'tone': self.tone or '',
            'current_finish': self.current_finish or '',
            'location_id': self.location_id.id if self.location_id else False,
            'reserved_origin': self.reserved_origin or workshop_order.sale_order_id.name or '',
            'state': 'pending',
        }

    def _sync_state_from_workshop_input(self):
        for selection in self:
            input_line = selection.workshop_input_line_id
            if not input_line:
                if selection.state != 'cancelled':
                    selection.state = 'selected'
                continue

            if input_line.state in ('sent_to_workshop', 'in_progress', 'partial_done', 'done'):
                selection.state = 'moved_to_workshop'
            elif input_line.state == 'reserved_for_workshop':
                selection.state = 'reserved'
            elif input_line.state == 'cancelled':
                selection.state = 'cancelled'
            elif selection.state != 'cancelled':
                selection.state = 'selected'

    def action_cancel_selection(self):
        for selection in self:
            input_line = selection.workshop_input_line_id
            if input_line and input_line.is_consumed:
                raise ValidationError(_(
                    'No puedes cancelar la selección del lote %s porque ya fue enviado/consumido en taller.'
                ) % selection.lot_id.name)

            if input_line and input_line.exists():
                input_line.unlink()

            selection.write({
                'state': 'cancelled',
                'workshop_input_line_id': False,
                'workshop_order_id': False,
            })

        return True
```

## ./models/stock_quant.py
```py
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
```

## ./models/workshop_order.py
```py
# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ACTIVE_WORKSHOP_STATES = (
    'validated',
    'confirmed',
    'sent_to_workshop',
    'in_progress',
    'partial_done',
)


class WorkshopOrder(models.Model):
    _inherit = 'workshop.order'

    sale_order_id = fields.Many2one(
        'sale.order',
        string='Orden de venta',
        index=True,
        copy=False,
        ondelete='set null',
    )
    sale_line_id = fields.Many2one(
        'sale.order.line',
        string='Línea de venta',
        index=True,
        copy=False,
        ondelete='set null',
    )
    sale_partner_id = fields.Many2one(
        related='sale_order_id.partner_id',
        string='Cliente',
        readonly=True,
        store=True,
    )
    sale_product_id = fields.Many2one(
        related='sale_line_id.product_id',
        string='Producto vendido',
        readonly=True,
        store=True,
    )
    sale_requested_qty = fields.Float(
        related='sale_line_id.product_uom_qty',
        string='Cantidad solicitada',
        readonly=True,
        store=True,
    )
    sale_workshop_reservation_picking_id = fields.Many2one(
        'stock.picking',
        string='Reserva de placas base',
        copy=False,
        readonly=True,
        help=(
            'Picking interno asignado, no validado, que reserva el producto base para esta OT. '
            'Se valida al enviar material a taller.'
        ),
    )
    sale_workshop_reserved = fields.Boolean(
        string='Base reservada',
        compute='_compute_sale_workshop_reserved',
    )
    sale_workshop_input_selection_ids = fields.One2many(
        'sale.stone.workshop.input.selection',
        'workshop_order_id',
        string='Selecciones origen desde venta',
        readonly=True,
    )

    @api.depends('sale_workshop_reservation_picking_id.state')
    def _compute_sale_workshop_reserved(self):
        for order in self:
            picking = order.sale_workshop_reservation_picking_id
            order.sale_workshop_reserved = bool(picking and picking.state not in ('cancel', 'done'))

    # ------------------------------------------------------------------
    # UI actions
    # ------------------------------------------------------------------

    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_('Esta orden de taller no está vinculada a una venta.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Orden de Venta'),
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': self.sale_order_id.id,
            'target': 'current',
        }

    def action_view_sale_workshop_reservation(self):
        self.ensure_one()
        if not self.sale_workshop_reservation_picking_id:
            raise UserError(_('No hay reserva de placas base para esta orden.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reserva de Placas Base'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.sale_workshop_reservation_picking_id.id,
            'target': 'current',
        }

    def action_reserve_inputs_for_sale(self):
        for order in self:
            order._sale_workshop_refresh_input_reservation()
        return True

    def action_release_sale_reservation(self):
        for order in self:
            order._sale_workshop_cancel_input_reservation(reset_lines=True)
        return True

    def action_assign_outputs_to_sale(self):
        assigned = self._sale_workshop_assign_outputs_to_sale()
        if not assigned:
            raise UserError(_('No se encontraron salidas finales recibidas para asignar a la línea de venta.'))
        return True

    # ------------------------------------------------------------------
    # Reservation picking helpers
    # ------------------------------------------------------------------

    def _sale_workshop_move_line_qty_vals(self, qty):
        StockMoveLine = self.env['stock.move.line']
        if 'quantity' in StockMoveLine._fields:
            return {'quantity': qty}
        if 'reserved_uom_qty' in StockMoveLine._fields:
            return {'reserved_uom_qty': qty}
        if 'qty_done' in StockMoveLine._fields:
            return {'qty_done': qty}
        return {}

    def _sale_workshop_move_qty_vals(self, product, qty):
        StockMove = self.env['stock.move']
        vals = {}
        if 'product_uom_qty' in StockMove._fields:
            vals['product_uom_qty'] = qty
        elif 'quantity' in StockMove._fields:
            vals['quantity'] = qty
        if 'product_uom_id' in StockMove._fields:
            vals['product_uom_id'] = product.uom_id.id
        elif 'product_uom' in StockMove._fields:
            vals['product_uom'] = product.uom_id.id
        return vals

    def _sale_workshop_input_lines_to_reserve(self):
        self.ensure_one()
        return self.input_line_ids.filtered(
            lambda l: l.state != 'cancelled'
            and not l.is_consumed
            and l.product_id
            and l.lot_id
            and (l.qty_in or 0.0) > 0.0
        )

    def _sale_workshop_sync_selection_states(self):
        selections = self.mapped('sale_workshop_input_selection_ids')
        if selections:
            selections._sync_state_from_workshop_input()
        return True

    def _sale_workshop_cancel_input_reservation(self, reset_lines=False):
        self.ensure_one()
        picking = self.sale_workshop_reservation_picking_id
        if not picking:
            return True
        if picking.state == 'done':
            if reset_lines:
                raise UserError(_(
                    'No se puede liberar la reserva porque el picking %s ya fue validado.'
                ) % picking.name)
            return True
        if picking.state != 'cancel':
            picking.action_cancel()
        if reset_lines:
            self.input_line_ids.filtered(
                lambda l: l.consume_picking_id == picking and not l.is_consumed
            ).with_context(skip_sale_workshop_reservation=True).write({
                'state': 'pending',
                'consume_picking_id': False,
            })
            self.write({'sale_workshop_reservation_picking_id': False})
            self._sale_workshop_sync_selection_states()
        return True

    def _sale_workshop_create_reservation_picking(self, input_lines):
        self.ensure_one()
        if not input_lines:
            return False
        self._ensure_default_locations()
        if not self.location_src_id or not self.location_workshop_id:
            raise UserError(_('Define ubicación origen y ubicación taller antes de reservar placas.'))

        picking_type = self._get_internal_picking_type()
        origin = '%s - Reserva taller' % (self.name or '')
        if self.sale_order_id:
            origin = '%s / %s - Reserva taller' % (self.sale_order_id.name, self.name)

        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': self.location_src_id.id,
            'location_dest_id': self.location_workshop_id.id,
            'origin': origin,
            'company_id': self.company_id.id,
        })

        moves = self.env['stock.move']
        move_specs = []
        StockMove = self.env['stock.move']
        StockMoveLine = self.env['stock.move.line']

        for line in input_lines:
            source_location = line.location_id or self.location_src_id
            move_vals = {
                'picking_id': picking.id,
                'product_id': line.product_id.id,
                'location_id': source_location.id,
                'location_dest_id': self.location_workshop_id.id,
                'company_id': self.company_id.id,
            }
            if 'name' in StockMove._fields:
                move_vals['name'] = '%s - Reserva %s' % (self.name, line.lot_id.name)
            elif 'description' in StockMove._fields:
                move_vals['description'] = '%s - Reserva %s' % (self.name, line.lot_id.name)
            move_vals.update(self._sale_workshop_move_qty_vals(line.product_id, line.qty_in))
            move = StockMove.create(move_vals)
            moves |= move
            move_specs.append((move, line, source_location))

        if moves:
            try:
                moves.with_context(skip_whole_lot=True)._action_confirm(merge=False)
            except TypeError:
                moves.with_context(skip_whole_lot=True)._action_confirm()

        for move, line, source_location in move_specs:
            # Evitar líneas autoasignadas por reglas externas y forzar el lote exacto.
            move.move_line_ids.unlink()
            ml_vals = {
                'move_id': move.id,
                'picking_id': picking.id,
                'product_id': line.product_id.id,
                'lot_id': line.lot_id.id,
                'location_id': source_location.id,
                'location_dest_id': self.location_workshop_id.id,
                'company_id': self.company_id.id,
            }
            if 'product_uom_id' in StockMoveLine._fields:
                ml_vals['product_uom_id'] = line.product_id.uom_id.id
            ml_vals.update(self._sale_workshop_move_line_qty_vals(line.qty_in))
            StockMoveLine.create(ml_vals)

        try:
            picking.action_assign()
        except Exception:
            _logger.exception('[STONE WORKSHOP SALE] No se pudo asignar automáticamente la reserva %s', picking.name)

        self.with_context(skip_sale_workshop_reservation=True).write({
            'sale_workshop_reservation_picking_id': picking.id,
        })
        input_lines.with_context(skip_sale_workshop_reservation=True).write({
            'state': 'reserved_for_workshop',
            'consume_picking_id': picking.id,
        })
        self._sale_workshop_sync_selection_states()
        self.message_post(body=_('Se reservaron placas base para venta en el picking %s.') % picking.name)
        return picking

    def _sale_workshop_refresh_input_reservation(self):
        for order in self:
            if self.env.context.get('skip_sale_workshop_reservation'):
                continue
            if not order.sale_order_id or not order.sale_line_id:
                continue
            if order.state in ('sent_to_workshop', 'in_progress', 'partial_done', 'done', 'cancel'):
                continue
            existing = order.sale_workshop_reservation_picking_id
            if existing and existing.state == 'done':
                continue
            input_lines = order._sale_workshop_input_lines_to_reserve()
            if not input_lines:
                order._sale_workshop_cancel_input_reservation(reset_lines=True)
                continue
            order._sale_workshop_cancel_input_reservation(reset_lines=False)
            order._sale_workshop_create_reservation_picking(input_lines)
        return True

    def _sale_workshop_reserved_qty_for_lot(self, product, lot):
        self.ensure_one()
        picking = self.sale_workshop_reservation_picking_id
        if not picking or picking.state in ('cancel', 'done'):
            return 0.0
        qty = 0.0
        for ml in picking.move_ids.move_line_ids.filtered(lambda l: l.product_id == product and l.lot_id == lot):
            if 'quantity' in ml._fields:
                qty += ml.quantity or 0.0
            elif 'reserved_uom_qty' in ml._fields:
                qty += ml.reserved_uom_qty or 0.0
            elif 'qty_done' in ml._fields:
                qty += ml.qty_done or 0.0
        return qty

    def _get_available_qty_for_lot(self, product, lot, location=False):
        qty = super()._get_available_qty_for_lot(product, lot, location=location)
        self.ensure_one()
        # La validación del taller debe considerar como disponible lo que ya está reservado
        # por esta misma OT; de lo contrario se bloquearía al validar/enviar a taller.
        return qty + self._sale_workshop_reserved_qty_for_lot(product, lot)

    # ------------------------------------------------------------------
    # Workshop flow hooks
    # ------------------------------------------------------------------

    def action_send_to_workshop(self):
        handled = self.env['workshop.order']
        for order in self:
            picking = order.sale_workshop_reservation_picking_id
            pending_inputs = order._sale_workshop_input_lines_to_reserve()
            if not order.sale_order_id or not picking or picking.state in ('cancel', 'done') or not pending_inputs:
                continue

            if order.state in ('draft', 'validated'):
                order.action_confirm()
            if order.state not in ('confirmed', 'sent_to_workshop'):
                raise UserError(_('Solo puedes enviar a taller órdenes confirmadas.'))
            order._validate_business_rules()
            order._validate_picking(picking)
            order.consume_picking_ids = [(4, picking.id)]
            pending_inputs.with_context(skip_sale_workshop_reservation=True).write({
                'state': 'sent_to_workshop',
                'is_consumed': True,
                'consume_picking_id': picking.id,
            })
            order.write({'state': 'sent_to_workshop'})
            order._sale_workshop_sync_selection_states()
            order.message_post(body=_('Material reservado enviado a taller con el picking %s.') % picking.name)
            handled |= order

        remaining = self - handled
        if remaining:
            return super(WorkshopOrder, remaining).action_send_to_workshop()
        return True

    def action_receive_outputs(self):
        res = super().action_receive_outputs()
        self._sale_workshop_assign_outputs_to_sale()
        return res

    def action_done(self):
        res = super().action_done()
        self._sale_workshop_assign_outputs_to_sale()
        return res

    def action_cancel(self):
        for order in self:
            picking = order.sale_workshop_reservation_picking_id
            if picking and picking.state not in ('done', 'cancel'):
                order._sale_workshop_cancel_input_reservation(reset_lines=True)
            order.sale_workshop_input_selection_ids.filtered(
                lambda s: s.state != 'moved_to_workshop'
            ).write({'state': 'cancelled'})
        return super().action_cancel()

    def _sale_workshop_assign_outputs_to_sale(self):
        assigned_any = False
        for order in self:
            sale_line = order.sale_line_id
            if not sale_line or not sale_line.exists() or not order.sale_order_id:
                continue
            output_lines = order.output_line_ids.filtered(
                lambda o: o.state in ('produced', 'received')
                and o.output_type not in ('scrap', 'rejected')
                and o.product_id == sale_line.product_id
                and o.lot_id
                and (o.qty_out or 0.0) > 0.0
            )
            if not output_lines:
                continue

            current_lot_ids = set(sale_line.lot_ids.ids) if hasattr(sale_line, 'lot_ids') else set()
            output_lot_ids = set(output_lines.mapped('lot_id').ids)
            lot_ids = list(current_lot_ids | output_lot_ids)

            breakdown = sale_line._stone_workshop_parse_breakdown() if hasattr(sale_line, '_stone_workshop_parse_breakdown') else {}
            for output in output_lines:
                key = str(output.lot_id.id)
                breakdown[key] = breakdown.get(key, 0.0) + (output.qty_out or 0.0)

            vals = {}
            if 'lot_ids' in sale_line._fields:
                vals['lot_ids'] = [(6, 0, lot_ids)]
            if 'x_lot_breakdown_json' in sale_line._fields:
                vals['x_lot_breakdown_json'] = breakdown
            if vals:
                sale_line.with_context(
                    skip_stone_workshop_product_defaults=True,
                    skip_sale_workshop_reservation=True,
                ).write(vals)
                if hasattr(sale_line, '_sync_lots_to_picking_moves'):
                    sale_line._sync_lots_to_picking_moves()
                assigned_any = True
                order.message_post(body=_('Se asignaron %(count)s lote(s) finales al pedido %(sale)s.') % {
                    'count': len(output_lot_ids),
                    'sale': order.sale_order_id.name,
                })
                order.sale_order_id.message_post(body=_(
                    'Taller %(workshop)s terminó producto final para la línea %(line)s. Lotes asignados: %(lots)s.'
                ) % {
                    'workshop': order.name,
                    'line': sale_line.product_id.display_name,
                    'lots': ', '.join(output_lines.mapped('lot_id.name')),
                })
        return assigned_any


class WorkshopInputLine(models.Model):
    _inherit = 'workshop.input.line'

    sale_order_id = fields.Many2one(
        related='order_id.sale_order_id',
        string='Orden de venta',
        store=True,
        readonly=True,
    )
    sale_line_id = fields.Many2one(
        related='order_id.sale_line_id',
        string='Línea de venta',
        store=True,
        readonly=True,
    )
    sale_workshop_input_selection_ids = fields.One2many(
        'sale.stone.workshop.input.selection',
        'workshop_input_line_id',
        string='Selección venta',
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('skip_sale_workshop_reservation'):
            lines.mapped('order_id')._sale_workshop_refresh_input_reservation()
        return lines

    def write(self, vals):
        orders = self.mapped('order_id')
        res = super().write(vals)
        if (
            not self.env.context.get('skip_sale_workshop_reservation')
            and any(key in vals for key in ('lot_id', 'product_id', 'qty_in', 'area_sqm', 'location_id', 'state'))
        ):
            (orders | self.mapped('order_id'))._sale_workshop_refresh_input_reservation()
        (orders | self.mapped('order_id'))._sale_workshop_sync_selection_states()
        return res

    def unlink(self):
        orders = self.mapped('order_id')
        selections = self.mapped('sale_workshop_input_selection_ids')
        res = super().unlink()
        if selections:
            selections.write({
                'workshop_input_line_id': False,
                'workshop_order_id': False,
                'state': 'selected',
            })
        if not self.env.context.get('skip_sale_workshop_reservation'):
            orders._sale_workshop_refresh_input_reservation()
        return res


class WorkshopOutputLine(models.Model):
    _inherit = 'workshop.output.line'

    sale_order_id = fields.Many2one(
        related='order_id.sale_order_id',
        string='Orden de venta',
        store=True,
        readonly=True,
    )
    sale_line_id = fields.Many2one(
        related='order_id.sale_line_id',
        string='Línea de venta',
        store=True,
        readonly=True,
    )
```

## ./static/src/components/workshop_input_selector/workshop_input_selector.js
```js
/** @odoo-module */

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, useState, onWillStart, onWillUpdateProps, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class SaleWorkshopInputSelector extends Component {
    static template = "sale_stone_workshop_integration.SaleWorkshopInputSelector";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this._popupRoot = null;
        this._keyHandler = null;
        this._searchTimeout = null;

        this.state = useState({
            isLoading: true,
            canSelect: false,
            tooltip: "Cargando...",
            selectedCount: 0,
            selectedQty: 0,
            data: null,
        });

        onWillStart(async () => {
            await this.loadLineData();
        });

        onWillUpdateProps(async () => {
            await this.loadLineData();
        });

        onWillUnmount(() => {
            this.destroyPopup();
        });
    }

    _getRecordId() {
        const rec = this.props.record;
        if (rec?.resId && typeof rec.resId === "number") {
            return rec.resId;
        }
        if (rec?.data?.id && typeof rec.data.id === "number") {
            return rec.data.id;
        }
        return null;
    }

    fmt(num) {
        if (num === null || num === undefined || isNaN(num)) {
            return "0.00";
        }
        return parseFloat(num).toFixed(2);
    }

    _escapeHtml(text) {
        if (text === null || text === undefined) {
            return "";
        }
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    async loadLineData() {
        const lineId = this._getRecordId();
        if (!lineId) {
            this.state.isLoading = false;
            this.state.canSelect = false;
            this.state.tooltip = "Guarda la línea antes de seleccionar placas base.";
            this.state.selectedCount = 0;
            this.state.selectedQty = 0;
            this.state.data = null;
            return;
        }

        this.state.isLoading = true;
        try {
            const data = await this.orm.call(
                "sale.order.line",
                "get_workshop_input_selector_data",
                [[lineId]]
            );

            this.state.data = data || {};
            this.state.canSelect = !!data?.can_select;
            this.state.selectedCount = data?.selected_count || 0;
            this.state.selectedQty = data?.selected_qty || 0;

            if (!data?.workshop_required) {
                this.state.tooltip = "Esta línea no requiere taller.";
            } else if (!data?.base_product_id) {
                this.state.tooltip = "Configura el producto base.";
            } else if (!data?.process_id) {
                this.state.tooltip = "Configura el proceso de taller.";
            } else if (!data?.can_select) {
                this.state.tooltip = "Solo disponible en órdenes confirmadas.";
            } else {
                this.state.tooltip = `Seleccionar placas base: ${data.base_product_name || ""}`;
            }
        } catch (error) {
            console.warn("[SWIS] Error cargando datos:", error);
            this.state.canSelect = false;
            this.state.tooltip = error.message || "No se pudo cargar la selección.";
        } finally {
            this.state.isLoading = false;
        }
    }

    _warn(message) {
        this.notification.add(message, { type: "warning" });
    }

    async openPopup() {
        await this.loadLineData();

        const data = this.state.data;
        if (!data || !data.workshop_required) {
            this._warn("Esta línea no requiere taller.");
            return;
        }
        if (!data.base_product_id) {
            this._warn("Configura el producto base antes de seleccionar placas.");
            return;
        }
        if (!data.process_id) {
            this._warn("Configura el proceso de taller antes de seleccionar placas.");
            return;
        }
        if (!data.can_select) {
            this._warn("La selección de placas base solo está disponible en órdenes de venta confirmadas.");
            return;
        }

        this.destroyPopup();

        const root = document.createElement("div");
        root.className = "swis-popup-root";
        document.body.appendChild(root);
        this._popupRoot = root;

        await this._renderPopup(data);
    }

    destroyPopup() {
        if (this._searchTimeout) {
            clearTimeout(this._searchTimeout);
            this._searchTimeout = null;
        }
        if (this._keyHandler) {
            document.removeEventListener("keydown", this._keyHandler);
            this._keyHandler = null;
        }
        if (this._popupRoot) {
            this._popupRoot.remove();
            this._popupRoot = null;
        }
    }

    async _renderPopup(data) {
        const root = this._popupRoot;
        const PAGE_SIZE = 35;

        const st = {
            items: [],
            total: 0,
            page: 0,
            isLoading: false,
            pendingIds: new Set(data.selected_lot_ids || []),
            pendingBreakdown: { ...(data.breakdown || {}) },
            filters: {
                lot_name: "",
                bloque: "",
                atado: "",
                alto_min: "",
                ancho_min: "",
                tipo: "",
            },
        };

        root.innerHTML = `
            <div class="swis-overlay" id="swis-overlay">
                <div class="swis-popup">
                    <div class="swis-header">
                        <div>
                            <div class="swis-title">
                                <i class="fa fa-cubes me-2"></i>
                                Placas base a consumir en taller
                            </div>
                            <div class="swis-subtitle">
                                Producto base: <strong>${this._escapeHtml(data.base_product_name || "")}</strong>
                                <span class="swis-sep">•</span>
                                Producto final: <strong>${this._escapeHtml(data.product_final_name || "")}</strong>
                            </div>
                        </div>
                        <div class="swis-header-actions">
                            <span class="swis-pill">
                                <i class="fa fa-bullseye me-1"></i>
                                Solicitado ${this.fmt(data.requested_qty || 0)}
                            </span>
                            <span class="swis-pill swis-pill-selected">
                                <i class="fa fa-check-circle me-1"></i>
                                <span id="swis-count">0</span> selec.
                            </span>
                            <span class="swis-pill swis-pill-total">
                                <i class="fa fa-balance-scale me-1"></i>
                                <span id="swis-total">0.00</span>
                            </span>
                            <button class="swis-btn swis-btn-primary" id="swis-confirm-top">
                                <i class="fa fa-check me-1"></i> Confirmar
                            </button>
                            <button class="swis-btn swis-btn-ghost" id="swis-close">
                                <i class="fa fa-times"></i>
                            </button>
                        </div>
                    </div>

                    <div class="swis-filters">
                        <div class="swis-filter">
                            <label>Lote</label>
                            <input id="swis-f-lot" type="text" placeholder="Buscar lote..."/>
                        </div>
                        <div class="swis-filter">
                            <label>Bloque</label>
                            <input id="swis-f-bloque" type="text" placeholder="Bloque..."/>
                        </div>
                        <div class="swis-filter">
                            <label>Atado</label>
                            <input id="swis-f-atado" type="text" placeholder="Atado..."/>
                        </div>
                        <div class="swis-filter swis-filter-sm">
                            <label>Alto mín.</label>
                            <input id="swis-f-alto" type="number" step="0.01"/>
                        </div>
                        <div class="swis-filter swis-filter-sm">
                            <label>Ancho mín.</label>
                            <input id="swis-f-ancho" type="number" step="0.01"/>
                        </div>
                        <div class="swis-filter swis-filter-sm">
                            <label>Tipo</label>
                            <select id="swis-f-tipo">
                                <option value="">Todos</option>
                                <option value="placa">Placa</option>
                                <option value="formato">Formato</option>
                                <option value="pieza">Pieza</option>
                                <option value="retazo">Retazo</option>
                            </select>
                        </div>
                        <div class="swis-filter-actions">
                            <button class="swis-btn swis-btn-light" id="swis-clear">
                                <i class="fa fa-square-o me-1"></i> Limpiar
                            </button>
                        </div>
                        <div class="swis-filter-stat" id="swis-stat">Buscando...</div>
                    </div>

                    <div class="swis-body" id="swis-body">
                        <div class="swis-empty">
                            <i class="fa fa-circle-o-notch fa-spin fa-2x"></i>
                            <div>Cargando inventario...</div>
                        </div>
                    </div>

                    <div class="swis-footer">
                        <span id="swis-footer-info">—</span>
                        <div class="swis-footer-actions">
                            <button class="swis-btn swis-btn-outline" id="swis-cancel">Cancelar</button>
                            <button class="swis-btn swis-btn-primary-dark" id="swis-confirm-bottom">
                                <i class="fa fa-check me-1"></i> Agregar selección
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        const close = () => this.destroyPopup();
        root.querySelector("#swis-close").addEventListener("click", close);
        root.querySelector("#swis-cancel").addEventListener("click", close);
        root.querySelector("#swis-overlay").addEventListener("click", (ev) => {
            if (ev.target.id === "swis-overlay") {
                close();
            }
        });

        this._keyHandler = (ev) => {
            if (ev.key === "Escape") {
                close();
            }
        };
        document.addEventListener("keydown", this._keyHandler);

        const updateSummary = () => {
            const count = st.pendingIds.size;
            let total = 0;
            for (const lotId of st.pendingIds) {
                const qty = parseFloat(st.pendingBreakdown[String(lotId)] || 0);
                total += qty || 0;
            }
            root.querySelector("#swis-count").textContent = String(count);
            root.querySelector("#swis-total").textContent = this.fmt(total);
        };

        const render = () => {
            const body = root.querySelector("#swis-body");
            const stat = root.querySelector("#swis-stat");
            const footer = root.querySelector("#swis-footer-info");

            if (st.isLoading && !st.items.length) {
                body.innerHTML = `
                    <div class="swis-empty">
                        <i class="fa fa-circle-o-notch fa-spin fa-2x"></i>
                        <div>Cargando inventario...</div>
                    </div>`;
                stat.innerHTML = '<i class="fa fa-circle-o-notch fa-spin me-1"></i> Buscando...';
                updateSummary();
                return;
            }

            if (!st.items.length) {
                body.innerHTML = `
                    <div class="swis-empty">
                        <i class="fa fa-inbox fa-2x"></i>
                        <div>No hay placas disponibles para este producto base.</div>
                    </div>`;
                stat.textContent = "0 lotes";
                footer.textContent = "Sin resultados disponibles.";
                updateSummary();
                return;
            }

            let rows = "";
            for (const q of st.items) {
                const lotId = q.lot_id ? q.lot_id[0] : 0;
                const lotName = q.lot_id ? q.lot_id[1] : "";
                if (!lotId) {
                    continue;
                }

                const selected = st.pendingIds.has(lotId);
                const tipo = String(q.x_tipo || "placa").toLowerCase();
                const isPartial = tipo === "formato" || tipo === "pieza" || tipo === "retazo";
                const qtyAvailable = parseFloat(q.quantity || 0);
                const qtyValue = parseFloat(st.pendingBreakdown[String(lotId)] || qtyAvailable || 0);
                const loc = q.location_id ? q.location_id[1] : "";

                rows += `
                    <tr class="${selected ? "swis-row-selected" : ""}" data-lot-id="${lotId}">
                        <td class="swis-col-check">
                            <input type="checkbox" ${selected ? "checked" : ""}/>
                        </td>
                        <td class="swis-lot">${this._escapeHtml(lotName)}</td>
                        <td>${this._escapeHtml(q.x_bloque || "-")}</td>
                        <td>${this._escapeHtml(q.x_atado || "-")}</td>
                        <td class="text-end">${q.x_alto ? this.fmt(q.x_alto) : "-"}</td>
                        <td class="text-end">${q.x_ancho ? this.fmt(q.x_ancho) : "-"}</td>
                        <td class="text-end swis-qty-available">${this.fmt(qtyAvailable)}</td>
                        <td>
                            <span class="swis-tag swis-tag-${this._escapeHtml(tipo)}">${this._escapeHtml(tipo || "placa")}</span>
                        </td>
                        <td>${this._escapeHtml(q.x_color || "-")}</td>
                        <td class="swis-location">${this._escapeHtml(loc)}</td>
                        <td class="text-end">
                            ${isPartial
                                ? `<input class="swis-qty-input" type="number" step="0.01" min="0" max="${qtyAvailable}" data-lot-id="${lotId}" value="${qtyValue || 0}"/>`
                                : `<span class="swis-fixed-qty">${this.fmt(qtyAvailable)}</span>`
                            }
                        </td>
                    </tr>
                `;
            }

            body.innerHTML = `
                <table class="swis-table">
                    <thead>
                        <tr>
                            <th></th>
                            <th>Lote</th>
                            <th>Bloque</th>
                            <th>Atado</th>
                            <th class="text-end">Alto</th>
                            <th class="text-end">Ancho</th>
                            <th class="text-end">Disp.</th>
                            <th>Tipo</th>
                            <th>Color</th>
                            <th>Ubicación</th>
                            <th class="text-end">A consumir</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            `;

            stat.textContent = `${st.total} lote(s)`;
            footer.textContent = `Mostrando ${st.items.length} de ${st.total} lote(s).`;

            body.querySelectorAll("tr[data-lot-id]").forEach((tr) => {
                tr.addEventListener("click", (ev) => {
                    if (ev.target.classList.contains("swis-qty-input")) {
                        return;
                    }

                    const lotId = parseInt(tr.dataset.lotId, 10);
                    const qtyText = tr.querySelector(".swis-qty-available")?.textContent || "0";
                    const qty = parseFloat(qtyText) || 0;

                    if (st.pendingIds.has(lotId)) {
                        st.pendingIds.delete(lotId);
                        delete st.pendingBreakdown[String(lotId)];
                    } else {
                        st.pendingIds.add(lotId);
                        st.pendingBreakdown[String(lotId)] = qty;
                    }

                    render();
                    updateSummary();
                });
            });

            body.querySelectorAll(".swis-qty-input").forEach((input) => {
                input.addEventListener("click", (ev) => ev.stopPropagation());
                input.addEventListener("input", (ev) => {
                    const lotId = parseInt(ev.target.dataset.lotId, 10);
                    const max = parseFloat(ev.target.max || 0);
                    let val = parseFloat(ev.target.value || 0);
                    if (val < 0) {
                        val = 0;
                    }
                    if (max && val > max) {
                        val = max;
                        ev.target.value = val;
                    }
                    if (val > 0) {
                        st.pendingIds.add(lotId);
                        st.pendingBreakdown[String(lotId)] = val;
                    } else {
                        st.pendingIds.delete(lotId);
                        delete st.pendingBreakdown[String(lotId)];
                    }
                    updateSummary();
                });
            });

            updateSummary();
        };

        const load = async (page = 0) => {
            st.isLoading = true;
            render();
            try {
                const result = await this.orm.call(
                    "stock.quant",
                    "search_stone_inventory_for_so_paginated",
                    [],
                    {
                        product_id: data.base_product_id,
                        filters: st.filters,
                        current_lot_ids: Array.from(st.pendingIds),
                        page,
                        page_size: PAGE_SIZE,
                    }
                );
                st.items = result?.items || [];
                st.total = result?.total || 0;
                st.page = page;
            } catch (error) {
                console.error("[SWIS] Error buscando inventario:", error);
                this.notification.add(error.message || "No se pudo cargar el inventario base.", { type: "danger" });
                st.items = [];
                st.total = 0;
            } finally {
                st.isLoading = false;
                render();
            }
        };

        const confirm = async () => {
            const lineId = this._getRecordId();
            if (!lineId) {
                this._warn("Guarda la línea antes de seleccionar placas base.");
                return;
            }

            try {
                const result = await this.orm.call(
                    "sale.order.line",
                    "write_workshop_input_selection_from_lots",
                    [[lineId]],
                    {
                        lot_ids: Array.from(st.pendingIds),
                        breakdown: st.pendingBreakdown,
                    }
                );
                this.state.data = result;
                this.state.selectedCount = result?.selected_count || 0;
                this.state.selectedQty = result?.selected_qty || 0;
                this.notification.add("Placas base actualizadas.", { type: "success" });
                this.destroyPopup();
                await this.loadLineData();
            } catch (error) {
                console.error("[SWIS] Error confirmando selección:", error);
                this.notification.add(error.message || "No se pudo guardar la selección.", { type: "danger" });
            }
        };

        root.querySelector("#swis-confirm-top").addEventListener("click", confirm);
        root.querySelector("#swis-confirm-bottom").addEventListener("click", confirm);

        root.querySelector("#swis-clear").addEventListener("click", () => {
            st.pendingIds = new Set();
            st.pendingBreakdown = {};
            render();
            updateSummary();
        });

        const bindFilter = (id, key) => {
            const el = root.querySelector(id);
            if (!el) {
                return;
            }
            el.addEventListener("input", () => {
                st.filters[key] = el.value || "";
                if (this._searchTimeout) {
                    clearTimeout(this._searchTimeout);
                }
                this._searchTimeout = setTimeout(() => load(0), 350);
            });
            el.addEventListener("change", () => {
                st.filters[key] = el.value || "";
                load(0);
            });
        };

        bindFilter("#swis-f-lot", "lot_name");
        bindFilter("#swis-f-bloque", "bloque");
        bindFilter("#swis-f-atado", "atado");
        bindFilter("#swis-f-alto", "alto_min");
        bindFilter("#swis-f-ancho", "ancho_min");
        bindFilter("#swis-f-tipo", "tipo");

        await load(0);
    }
}

registry.category("fields").add("sale_workshop_input_selector", {
    component: SaleWorkshopInputSelector,
    supportedTypes: ["boolean"],
});
```

## ./static/src/components/workshop_input_selector/workshop_input_selector.xml
```xml
<?xml version="1.0" encoding="UTF-8"?>
<templates xml:space="preserve">
    <t t-name="sale_stone_workshop_integration.SaleWorkshopInputSelector" owl="1">
        <div class="swis-field">
            <button type="button"
                    class="swis-inline-btn"
                    t-att-class="state.canSelect ? 'swis-ready' : 'swis-disabled'"
                    t-att-title="state.tooltip"
                    t-on-click.stop.prevent="openPopup">
                <i class="fa fa-cubes"/>
                <span t-if="state.isLoading" class="swis-text">...</span>
                <span t-elif="state.selectedCount" class="swis-text">
                    <t t-esc="state.selectedCount"/> base · <t t-esc="fmt(state.selectedQty)"/>
                </span>
                <span t-else="" class="swis-text">Base</span>
            </button>
        </div>
    </t>
</templates>
```

## ./static/src/scss/workshop_input_selector.scss
```scss
/* Sale Stone Workshop Integration — selector visual de placas base */

.swis-field {
    display: inline-flex;
    align-items: center;
}

.swis-inline-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 26px;
    padding: 3px 9px;
    border-radius: 999px;
    border: 1px solid #d1d5db;
    background: #ffffff;
    color: #374151;
    font-size: 12px;
    font-weight: 700;
    line-height: 1;
    white-space: nowrap;
    cursor: pointer;
}

.swis-inline-btn.swis-ready {
    border-color: #9ca3af;
    background: #f9fafb;
    color: #111827;
}

.swis-inline-btn.swis-ready:hover {
    background: #111827;
    color: #ffffff;
    border-color: #111827;
}

.swis-inline-btn.swis-disabled {
    opacity: .55;
    cursor: not-allowed;
}

.swis-inline-btn .fa {
    font-size: 12px;
}

.swis-popup-root,
.swis-overlay {
    position: fixed;
    inset: 0;
    z-index: 1080;
}

.swis-overlay {
    background: rgba(17, 24, 39, .42);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
}

.swis-popup {
    width: min(1380px, 96vw);
    height: min(820px, 92vh);
    background: #ffffff;
    border-radius: 18px;
    border: 1px solid rgba(17, 24, 39, .12);
    box-shadow: 0 24px 70px rgba(17, 24, 39, .26);
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

.swis-header {
    padding: 16px 18px;
    border-bottom: 1px solid #e5e7eb;
    background: linear-gradient(180deg, #ffffff 0%, #f9fafb 100%);
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 18px;
}

.swis-title {
    font-size: 18px;
    font-weight: 900;
    color: #111827;
    letter-spacing: -.02em;
}

.swis-subtitle {
    margin-top: 4px;
    color: #6b7280;
    font-size: 12px;
}

.swis-sep {
    margin: 0 8px;
    color: #9ca3af;
}

.swis-header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: flex-end;
}

.swis-pill {
    display: inline-flex;
    align-items: center;
    border: 1px solid #e5e7eb;
    background: #ffffff;
    color: #374151;
    border-radius: 999px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 800;
}

.swis-pill-selected {
    background: #f0fdf4;
    border-color: #bbf7d0;
    color: #166534;
}

.swis-pill-total {
    background: #eff6ff;
    border-color: #bfdbfe;
    color: #1d4ed8;
}

.swis-btn {
    border: 1px solid #d1d5db;
    background: #ffffff;
    color: #111827;
    border-radius: 10px;
    padding: 7px 11px;
    font-size: 12px;
    font-weight: 800;
    cursor: pointer;
    line-height: 1;
}

.swis-btn:hover {
    background: #f3f4f6;
}

.swis-btn-primary,
.swis-btn-primary-dark {
    background: #111827;
    border-color: #111827;
    color: #ffffff;
}

.swis-btn-primary:hover,
.swis-btn-primary-dark:hover {
    background: #374151;
    border-color: #374151;
}

.swis-btn-ghost {
    width: 34px;
    height: 34px;
    padding: 0;
    display: inline-flex;
    align-items: center;
    justify-content: center;
}

.swis-btn-light {
    background: #f9fafb;
}

.swis-btn-outline {
    background: #ffffff;
}

.swis-filters {
    display: grid;
    grid-template-columns: minmax(160px, 1.2fr) minmax(130px, .9fr) minmax(120px, .8fr) 90px 90px 110px auto 1fr;
    gap: 10px;
    align-items: end;
    padding: 12px 18px;
    border-bottom: 1px solid #e5e7eb;
    background: #ffffff;
}

.swis-filter {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.swis-filter label {
    margin: 0;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: .04em;
    color: #6b7280;
    font-weight: 900;
}

.swis-filter input,
.swis-filter select {
    width: 100%;
    border: 1px solid #d1d5db;
    border-radius: 9px;
    padding: 7px 9px;
    min-height: 34px;
    font-size: 12px;
    color: #111827;
    background: #ffffff;
}

.swis-filter input:focus,
.swis-filter select:focus {
    outline: none;
    border-color: #111827;
    box-shadow: 0 0 0 2px rgba(17, 24, 39, .08);
}

.swis-filter-actions {
    display: flex;
    align-items: center;
    gap: 6px;
}

.swis-filter-stat {
    justify-self: end;
    color: #6b7280;
    font-size: 12px;
    font-weight: 700;
}

.swis-body {
    flex: 1;
    overflow: auto;
    background: #f9fafb;
}

.swis-empty {
    min-height: 260px;
    display: flex;
    gap: 10px;
    color: #6b7280;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    font-weight: 700;
}

.swis-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    background: #ffffff;
    font-size: 12px;
}

.swis-table thead th {
    position: sticky;
    top: 0;
    z-index: 2;
    background: #f3f4f6;
    border-bottom: 1px solid #d1d5db;
    color: #374151;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: .04em;
    font-weight: 900;
    padding: 9px 8px;
    white-space: nowrap;
}

.swis-table tbody td {
    border-bottom: 1px solid #f3f4f6;
    padding: 8px;
    vertical-align: middle;
    color: #374151;
}

.swis-table tbody tr {
    cursor: pointer;
}

.swis-table tbody tr:hover {
    background: #f9fafb;
}

.swis-table tbody tr.swis-row-selected {
    background: #eff6ff;
}

.swis-col-check {
    width: 38px;
    text-align: center;
}

.swis-lot {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
    font-weight: 900;
    color: #111827;
}

.swis-location {
    max-width: 230px;
    color: #6b7280;
    font-size: 11px;
}

.swis-tag {
    display: inline-flex;
    align-items: center;
    padding: 3px 7px;
    border-radius: 999px;
    background: #f3f4f6;
    color: #374151;
    font-size: 10px;
    font-weight: 900;
    text-transform: uppercase;
}

.swis-qty-input {
    width: 88px;
    border: 1px solid #d1d5db;
    border-radius: 8px;
    padding: 5px 7px;
    font-size: 12px;
    text-align: right;
}

.swis-qty-input:focus {
    outline: none;
    border-color: #111827;
    box-shadow: 0 0 0 2px rgba(17, 24, 39, .08);
}

.swis-fixed-qty {
    font-weight: 900;
    color: #111827;
}

.swis-footer {
    padding: 12px 18px;
    border-top: 1px solid #e5e7eb;
    background: #ffffff;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-size: 12px;
    color: #6b7280;
    font-weight: 700;
}

.swis-footer-actions {
    display: flex;
    align-items: center;
    gap: 8px;
}

@media (max-width: 980px) {
    .swis-popup {
        width: 98vw;
        height: 96vh;
        border-radius: 12px;
    }

    .swis-header,
    .swis-footer {
        flex-direction: column;
        align-items: stretch;
    }

    .swis-header-actions,
    .swis-footer-actions {
        justify-content: flex-start;
    }

    .swis-filters {
        grid-template-columns: repeat(2, minmax(120px, 1fr));
    }
}
```

## ./views/sale_order_views.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_sale_order_form_stone_workshop_integration" model="ir.ui.view">
        <field name="name">sale.order.form.stone.workshop.integration</field>
        <field name="model">sale.order</field>
        <field name="inherit_id" ref="sale.view_order_form"/>
        <field name="priority">120</field>
        <field name="arch" type="xml">

            <xpath expr="//header" position="inside">
                <button name="action_create_stone_workshop_orders"
                        string="Crear OT taller"
                        type="object"
                        class="btn-secondary"
                        invisible="state not in ('sale', 'done')"/>
                <button name="action_sync_workshop_input_selections"
                        string="Sincronizar placas taller"
                        type="object"
                        class="btn-secondary"
                        invisible="state not in ('sale', 'done') or stone_workshop_input_selection_count == 0"/>
            </xpath>

            <xpath expr="//div[@name='button_box']" position="inside">
                <button name="action_view_stone_workshop_orders"
                        type="object"
                        class="oe_stat_button"
                        icon="fa-industry"
                        invisible="stone_workshop_order_count == 0">
                    <field name="stone_workshop_order_count"
                           widget="statinfo"
                           string="Taller"/>
                </button>
            </xpath>

            <xpath expr="//field[@name='order_line']/list/field[@name='product_id']" position="after">
                <field name="stone_workshop_required"
                       string="Requiere taller"
                       widget="boolean_toggle"
                       optional="show"/>

                <field name="stone_workshop_assignment_state"
                       string="Estado taller"
                       optional="show"
                       widget="badge"
                       decoration-info="stone_workshop_assignment_state in ('pending_inputs', 'outputs_pending')"
                       decoration-warning="stone_workshop_assignment_state in ('reserved_inputs', 'in_workshop')"
                       decoration-success="stone_workshop_assignment_state == 'assigned'"
                       decoration-muted="stone_workshop_assignment_state in ('none', 'cancelled')"/>

                <field name="stone_workshop_input_selector_anchor"
                       string="Placas base"
                       widget="sale_workshop_input_selector"
                       optional="show"/>

                <field name="stone_workshop_input_selection_count"
                       string="# Base"
                       optional="hide"
                       readonly="1"/>

                <field name="stone_workshop_input_selection_total_qty"
                       string="M² Base"
                       optional="hide"
                       readonly="1"/>

                <field name="stone_workshop_base_product_id"
                       string="Producto base"
                       optional="hide"/>

                <field name="stone_workshop_process_id"
                       string="Proceso taller"
                       optional="hide"/>

                <field name="stone_workshop_order_id"
                       string="OT taller"
                       optional="show"
                       readonly="1"/>

                <field name="stone_workshop_auto_create" column_invisible="True"/>
                <field name="stone_workshop_operation_mode" column_invisible="True"/>
                <field name="stone_workshop_trigger" column_invisible="True"/>
                <field name="stone_workshop_commercial_mode" column_invisible="True"/>
                <field name="stone_workshop_service_product_id" column_invisible="True"/>
                <field name="stone_is_workshop_service_line" column_invisible="True"/>
                <field name="stone_workshop_parent_line_id" column_invisible="True"/>
                <field name="stone_workshop_hide_from_customer" column_invisible="True"/>
            </xpath>

            <xpath expr="//notebook" position="inside">
                <page string="Taller"
                      name="stone_workshop_orders"
                      invisible="stone_workshop_order_count == 0">

                    <group>
                        <group>
                            <field name="stone_workshop_order_count" readonly="1"/>
                            <field name="stone_workshop_pending_count" readonly="1"/>
                        </group>
                    </group>

                    <field name="stone_workshop_order_ids" readonly="1">
                        <list decoration-success="state == 'done'"
                              decoration-warning="state in ('confirmed', 'sent_to_workshop', 'in_progress', 'partial_done')"
                              decoration-muted="state == 'cancel'">

                            <field name="name"/>
                            <field name="sale_line_id"/>
                            <field name="input_product_id"/>
                            <field name="default_product_out_id"/>
                            <field name="process_id"/>
                            <field name="area_in_total"/>
                            <field name="area_out_total"/>
                            <field name="sale_workshop_reserved" widget="boolean_toggle"/>
                            <field name="state" widget="badge"/>
                        </list>
                    </field>
                </page>

                <page string="Placas base taller"
                      name="stone_workshop_input_selections"
                      invisible="state not in ('sale', 'done') and stone_workshop_input_selection_count == 0">

                    <group>
                        <group>
                            <field name="stone_workshop_input_selection_count" readonly="1"/>
                            <field name="stone_workshop_input_selection_total_qty" readonly="1"/>
                        </group>
                    </group>

                    <div class="alert alert-info" role="alert">
                        Esta sección muestra las placas, formatos o retazos del <strong>producto base</strong>
                        que serán consumidos por taller para fabricar el producto final vendido al cliente.
                        No son los lotes finales de entrega.
                    </div>

                    <field name="stone_workshop_input_selection_ids" readonly="1">
                        <list decoration-success="state in ('reserved', 'moved_to_workshop')"
                              decoration-muted="state == 'cancelled'">
                            <field name="sale_line_id"/>
                            <field name="product_final_id"/>
                            <field name="base_product_id"/>
                            <field name="lot_id"/>
                            <field name="location_id"/>
                            <field name="material_type"/>
                            <field name="qty_in"/>
                            <field name="area_sqm"/>
                            <field name="block_name"/>
                            <field name="workshop_order_id"/>
                            <field name="workshop_input_line_id"/>
                            <field name="consume_picking_id"/>
                            <field name="state" widget="badge"/>
                        </list>
                    </field>
                </page>
            </xpath>

        </field>
    </record>
</odoo>
```

## ./views/workshop_order_views.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_workshop_order_form_sale_integration" model="ir.ui.view">
        <field name="name">workshop.order.form.sale.integration</field>
        <field name="model">workshop.order</field>
        <field name="inherit_id" ref="stone_workshop.view_workshop_order_form"/>
        <field name="arch" type="xml">
            <xpath expr="//header" position="inside">
                <button name="action_reserve_inputs_for_sale"
                        string="Reservar placas venta"
                        type="object"
                        class="btn-secondary"
                        invisible="not sale_order_id or state not in ('draft', 'validated', 'confirmed')"/>
                <button name="action_release_sale_reservation"
                        string="Liberar reserva"
                        type="object"
                        invisible="not sale_workshop_reserved or state not in ('draft', 'validated', 'confirmed')"/>
                <button name="action_assign_outputs_to_sale"
                        string="Asignar a pedido"
                        type="object"
                        class="btn-secondary"
                        invisible="not sale_order_id or state not in ('partial_done', 'done')"/>
            </xpath>

            <xpath expr="//div[@name='button_box']" position="inside">
                <button name="action_view_sale_order"
                        type="object"
                        class="oe_stat_button"
                        icon="fa-shopping-cart"
                        invisible="not sale_order_id">
                    <div class="o_stat_info">
                        <span class="o_stat_text">Venta</span>
                        <span class="o_stat_value"><field name="sale_order_id" readonly="1"/></span>
                    </div>
                </button>
                <button name="action_view_sale_workshop_reservation"
                        type="object"
                        class="oe_stat_button"
                        icon="fa-lock"
                        invisible="not sale_workshop_reservation_picking_id">
                    <div class="o_stat_info">
                        <span class="o_stat_text">Reserva</span>
                        <span class="o_stat_value"><field name="sale_workshop_reservation_picking_id" readonly="1"/></span>
                    </div>
                </button>
            </xpath>

            <xpath expr="//field[@name='process_id']" position="after">
                <field name="sale_order_id" readonly="1"/>
                <field name="sale_line_id" readonly="1"/>
                <field name="sale_partner_id" readonly="1"/>
                <field name="sale_requested_qty" readonly="1"/>
                <field name="sale_workshop_reserved" readonly="1"/>
            </xpath>

            <xpath expr="//field[@name='input_line_ids']/list/field[@name='reserved_origin']" position="after">
                <field name="sale_order_id" optional="hide"/>
                <field name="sale_line_id" optional="hide"/>
            </xpath>

            <xpath expr="//field[@name='output_line_ids']/list/field[@name='state']" position="after">
                <field name="sale_order_id" optional="hide"/>
                <field name="sale_line_id" optional="hide"/>
            </xpath>
        </field>
    </record>

    <record id="view_workshop_order_list_sale_integration" model="ir.ui.view">
        <field name="name">workshop.order.list.sale.integration</field>
        <field name="model">workshop.order</field>
        <field name="inherit_id" ref="stone_workshop.view_workshop_order_list"/>
        <field name="arch" type="xml">
            <xpath expr="//field[@name='name']" position="after">
                <field name="sale_order_id" optional="show"/>
                <field name="sale_partner_id" optional="hide"/>
            </xpath>
        </field>
    </record>
</odoo>
```

