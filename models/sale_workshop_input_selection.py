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
        index=True,
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
        string='Largo',
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
            # Stock de la compañía de la venta (ubicaciones compartidas incluidas).
            if line.company_id:
                domain.append(('company_id', 'in', [line.company_id.id, False]))

            qty = 0.0
            for quant in self.env['stock.quant'].search(domain):
                reserved = quant.reserved_quantity if 'reserved_quantity' in quant._fields else 0.0
                qty += (quant.quantity or 0.0) - (reserved or 0.0)

            # Reserva DÉBIL: lo retenido por traslados internos de
            # carrito/escáner ABIERTOS sigue contando como disponible
            # (se libera solo cuando la venta/taller toma el lote).
            weak_domain = [
                ('product_id', '=', line.base_product_id.id),
                ('lot_id', '=', line.lot_id.id),
                ('state', 'in', ('assigned', 'partially_available')),
                ('picking_id.picking_type_code', '=', 'internal'),
                ('picking_id.origin', '=like', 'Carrito - %'),
                ('picking_id.state', 'not in', ('done', 'cancel')),
            ]
            if line.location_id:
                weak_domain.append(('location_id', 'child_of', line.location_id.id))
            if line.company_id:
                weak_domain.append(('company_id', '=', line.company_id.id))
            weak_lines = self.env['stock.move.line'].sudo().search(weak_domain)
            qty += sum(weak_lines.mapped('quantity'))

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

            if input_line.state in ('in_progress', 'done'):
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
                # sudo: borrar la línea de entrada de la OT es plomería. El
                # unlink de workshop.input.line solo lo tiene el SUPERVISOR
                # de taller — sin esto, cancelar una selección de placa
                # tronaba hasta para el propio usuario de taller.
                input_line.sudo().unlink()

            selection.write({
                'state': 'cancelled',
                'workshop_input_line_id': False,
                'workshop_order_id': False,
            })

            # Si la selección venía de una asignación de TRÁNSITO (Transit
            # Allocation → filtro Taller), liberar la reserva del material
            # en el embarque para que vuelva a estar disponible.
            transit_lines = self.env['stock.transit.line'].sudo().search([
                ('workshop_sale_line_id', '=', selection.sale_line_id.id),
                ('lot_id', '=', selection.lot_id.id),
                ('allocation_status', '=', 'reserved'),
            ])
            if transit_lines:
                transit_lines.with_context(skip_reservation_logic=True).write({
                    'allocation_status': 'available',
                    'workshop_sale_line_id': False,
                })
                # Quant publicado: si ninguna otra gemela lo compromete,
                # regresa a disponible en el inventario visual.
                for tl in transit_lines:
                    quant = tl.quant_id
                    if not quant or not quant.exists():
                        continue
                    if 'transit_inventory_state' not in quant._fields \
                            or not getattr(quant, 'transit_inventory_published', False):
                        continue
                    sibling = self.env['stock.transit.line'].sudo().search([
                        ('quant_id', '=', quant.id),
                        ('id', 'not in', transit_lines.ids),
                        '|', '|',
                        ('order_id', '!=', False),
                        ('partner_id', '!=', False),
                        ('allocation_status', '=', 'reserved'),
                    ], limit=1)
                    if not sibling:
                        quant.sudo().write({'transit_inventory_state': 'available'})

        return True
