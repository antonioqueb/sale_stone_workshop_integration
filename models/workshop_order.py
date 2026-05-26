# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

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
            order.sale_workshop_reserved = bool(
                picking and picking.state not in ('cancel', 'done')
            )

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
        assigned = self._sale_workshop_assign_outputs_to_sale(manual=True)

        if not assigned:
            raise UserError(_('No se encontraron salidas finales recibidas para asignar a la línea de venta.'))

        return True

    # ------------------------------------------------------------------
    # Reservation picking helpers
    # ------------------------------------------------------------------

    def _sale_workshop_stock_context(self):
        return {
            'skip_whole_lot': True,
            'skip_whole_lot_removal': True,
            'skip_sale_workshop_reservation': True,
            'skip_lot_duplicate_check': True,
            'skip_stock_lot_duplicate_check': True,
        }

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
            lambda l:
                l.state != 'cancelled'
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
            picking.with_context(**self._sale_workshop_stock_context()).action_cancel()

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
        """
        Crea el picking interno de reserva para placas base seleccionadas desde venta.

        Punto clave:
        stock_whole_lot_removal puede generar líneas automáticas al confirmar/asignar.
        Luego, al crear las líneas exactas seleccionadas desde taller, stock_lot_dimensions
        puede bloquear por duplicidad dentro del mismo picking.

        Este flujo evita el choque:
        1. Crea movimientos.
        2. Confirma movimientos con flags de bypass.
        3. Limpia cualquier línea automática.
        4. Crea únicamente las líneas exactas seleccionadas por la venta/taller.
        5. No ejecuta picking.action_assign() después de crear las líneas exactas.
        """
        self.ensure_one()

        if not input_lines:
            return False

        self._ensure_default_locations()

        if not self.location_src_id or not self.location_workshop_id:
            raise UserError(_('Define ubicación origen y ubicación taller antes de reservar placas.'))

        picking_type = self._get_internal_picking_type()
        bypass_ctx = self._sale_workshop_stock_context()

        origin = '%s - Reserva taller' % (self.name or '')
        if self.sale_order_id:
            origin = '%s / %s - Reserva taller' % (self.sale_order_id.name, self.name)

        picking = self.env['stock.picking'].with_context(**bypass_ctx).create({
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

            move = StockMove.with_context(**bypass_ctx).create(move_vals)
            moves |= move
            move_specs.append((move, line, source_location))

        if moves:
            try:
                moves.with_context(**bypass_ctx)._action_confirm(merge=False)
            except TypeError:
                moves.with_context(**bypass_ctx)._action_confirm()

        # Si algún módulo creó líneas automáticas al confirmar, se eliminan antes de crear la reserva exacta.
        auto_lines = moves.mapped('move_line_ids')
        if auto_lines:
            _logger.info(
                '[STONE WORKSHOP SALE] Eliminando %s línea(s) automática(s) del picking %s antes de crear reserva exacta.',
                len(auto_lines),
                picking.name,
            )
            auto_lines.with_context(**bypass_ctx).unlink()

        created_move_lines = self.env['stock.move.line']

        for move, line, source_location in move_specs:
            # Limpieza defensiva por si algún hook creó líneas en el movimiento específico.
            if move.move_line_ids:
                move.move_line_ids.with_context(**bypass_ctx).unlink()

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

            move_line = StockMoveLine.with_context(**bypass_ctx).create(ml_vals)
            created_move_lines |= move_line

        _logger.info(
            '[STONE WORKSHOP SALE] Reserva exacta creada para %s. Moves=%s MoveLines=%s',
            picking.name,
            moves.ids,
            created_move_lines.ids,
        )

        self.with_context(skip_sale_workshop_reservation=True).write({
            'sale_workshop_reservation_picking_id': picking.id,
        })

        input_lines.with_context(skip_sale_workshop_reservation=True).write({
            'state': 'reserved_for_workshop',
            'consume_picking_id': picking.id,
        })

        self._sale_workshop_sync_selection_states()

        self.message_post(
            body=_('Se reservaron placas base para venta en el picking %s.') % picking.name
        )

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

    # ------------------------------------------------------------------
    # Stock validation helpers
    # ------------------------------------------------------------------

    def _sale_workshop_reserved_qty_for_lot(self, product, lot, location=False):
        """
        Cantidad reservada por el picking interno de esta misma OT.

        Esta cantidad cuenta como disponible para esta OT porque ya fue apartada
        precisamente para enviarse a taller.
        """
        self.ensure_one()

        picking = self.sale_workshop_reservation_picking_id
        if not picking or picking.state in ('cancel', 'done'):
            return 0.0

        location_ids = False
        if location:
            location_ids = set(
                self.env['stock.location'].search([
                    ('id', 'child_of', location.id),
                ]).ids
            )

        qty = 0.0

        for ml in picking.move_ids.move_line_ids.filtered(
            lambda l:
                l.product_id == product
                and l.lot_id == lot
        ):
            if location_ids and ml.location_id.id not in location_ids:
                continue

            if 'quantity' in ml._fields:
                qty += ml.quantity or 0.0
            elif 'reserved_uom_qty' in ml._fields:
                qty += ml.reserved_uom_qty or 0.0
            elif 'qty_done' in ml._fields:
                qty += ml.qty_done or 0.0

        return qty

    def _sale_workshop_quant_qty_for_lot(self, product, lot, location=False):
        self.ensure_one()

        domain = [
            ('product_id', '=', product.id),
            ('lot_id', '=', lot.id),
            ('location_id.usage', '=', 'internal'),
        ]

        if location:
            domain.append(('location_id', 'child_of', location.id))

        total_qty = 0.0
        reserved_qty = 0.0

        quants = self.env['stock.quant'].sudo().search(domain)

        for quant in quants:
            total_qty += quant.quantity or 0.0

            if 'reserved_quantity' in quant._fields:
                reserved_qty += quant.reserved_quantity or 0.0

        return {
            'total_qty': total_qty,
            'reserved_qty': reserved_qty,
            'free_qty': total_qty - reserved_qty,
            'quant_count': len(quants),
        }

    def _sale_workshop_effective_available_qty_for_input_line(self, input_line):
        self.ensure_one()

        if not input_line.product_id or not input_line.lot_id:
            return {
                'total_qty': 0.0,
                'reserved_qty': 0.0,
                'free_qty': 0.0,
                'own_reserved_qty': 0.0,
                'effective_available_qty': 0.0,
                'quant_count': 0,
            }

        location = input_line.location_id or self.location_src_id

        quant_data = self._sale_workshop_quant_qty_for_lot(
            input_line.product_id,
            input_line.lot_id,
            location=location,
        )

        own_reserved_qty = self._sale_workshop_reserved_qty_for_lot(
            input_line.product_id,
            input_line.lot_id,
            location=location,
        )

        effective_available_qty = (quant_data.get('free_qty') or 0.0) + own_reserved_qty

        return {
            'total_qty': quant_data.get('total_qty') or 0.0,
            'reserved_qty': quant_data.get('reserved_qty') or 0.0,
            'free_qty': quant_data.get('free_qty') or 0.0,
            'own_reserved_qty': own_reserved_qty,
            'effective_available_qty': effective_available_qty,
            'quant_count': quant_data.get('quant_count') or 0,
        }

    def _sale_workshop_assert_input_line_effective_available(self, input_line):
        self.ensure_one()

        qty_required = input_line.qty_in or 0.0
        qty_data = self._sale_workshop_effective_available_qty_for_input_line(input_line)

        rounding = (
            input_line.product_id.uom_id.rounding
            if input_line.product_id and input_line.product_id.uom_id
            else 0.00001
        )

        _logger.info(
            '[STONE WORKSHOP SALE STOCK] order=%s input_line=%s product=%s lot=%s '
            'total=%s reserved=%s free=%s own_reserved=%s effective=%s required=%s location=%s',
            self.name,
            input_line.id,
            input_line.product_id.display_name if input_line.product_id else '',
            input_line.lot_id.name if input_line.lot_id else '',
            qty_data.get('total_qty'),
            qty_data.get('reserved_qty'),
            qty_data.get('free_qty'),
            qty_data.get('own_reserved_qty'),
            qty_data.get('effective_available_qty'),
            qty_required,
            input_line.location_id.display_name if input_line.location_id else (
                self.location_src_id.display_name if self.location_src_id else ''
            ),
        )

        if float_compare(
            qty_data.get('effective_available_qty') or 0.0,
            qty_required,
            precision_rounding=rounding,
        ) < 0:
            raise UserError(_(
                'No hay existencias suficientes para el lote %(lot)s.\n\n'
                'Producto: %(product)s\n'
                'Existencia total: %(total)s\n'
                'Reservado total: %(reserved)s\n'
                'Libre real: %(free)s\n'
                'Reservado por esta OT: %(own_reserved)s\n'
                'Disponible efectivo: %(effective)s\n'
                'Requerido: %(required)s'
            ) % {
                'lot': input_line.lot_id.name if input_line.lot_id else '',
                'product': input_line.product_id.display_name if input_line.product_id else '',
                'total': qty_data.get('total_qty') or 0.0,
                'reserved': qty_data.get('reserved_qty') or 0.0,
                'free': qty_data.get('free_qty') or 0.0,
                'own_reserved': qty_data.get('own_reserved_qty') or 0.0,
                'effective': qty_data.get('effective_available_qty') or 0.0,
                'required': qty_required,
            })

        return True

    def _sale_workshop_all_inputs_covered_by_own_reservation(self):
        self.ensure_one()

        input_lines = self.input_line_ids.filtered(
            lambda l:
                l.state != 'cancelled'
                and not l.is_consumed
                and l.product_id
                and l.lot_id
                and (l.qty_in or 0.0) > 0.0
        )

        if not input_lines:
            return False

        for input_line in input_lines:
            self._sale_workshop_assert_input_line_effective_available(input_line)

        return True

    def _get_available_qty_for_lot(self, product, lot, location=False):
        qty = super()._get_available_qty_for_lot(product, lot, location=location)
        self.ensure_one()

        return qty + self._sale_workshop_reserved_qty_for_lot(
            product,
            lot,
            location=location,
        )

    def _validate_business_rules(self):
        """
        Evita falso negativo cuando el material ya está reservado por el picking
        interno de esta misma OT.
        """
        try:
            return super()._validate_business_rules()
        except UserError as error:
            message = str(error).lower()

            stock_error_terms = (
                'existencia',
                'existencias',
                'stock',
                'disponible',
                'insuficiente',
                'suficiente',
            )

            is_stock_error = any(term in message for term in stock_error_terms)

            if not is_stock_error:
                raise

            for order in self:
                if (
                    order.sale_order_id
                    and order.sale_workshop_reservation_picking_id
                    and order._sale_workshop_all_inputs_covered_by_own_reservation()
                ):
                    _logger.warning(
                        '[STONE WORKSHOP SALE] Se ignoró falso negativo de stock en %s '
                        'porque los insumos están cubiertos por su propia reserva %s. '
                        'Error original: %s',
                        order.name,
                        order.sale_workshop_reservation_picking_id.name,
                        str(error),
                    )
                    continue

                raise

            return True

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

            order.with_context(**order._sale_workshop_stock_context())._validate_picking(picking)

            order.consume_picking_ids = [(4, picking.id)]

            pending_inputs.with_context(skip_sale_workshop_reservation=True).write({
                'state': 'sent_to_workshop',
                'is_consumed': True,
                'consume_picking_id': picking.id,
            })

            order.write({'state': 'sent_to_workshop'})
            order._sale_workshop_sync_selection_states()

            order.message_post(
                body=_('Material reservado enviado a taller con el picking %s.') % picking.name
            )

            handled |= order

        remaining = self - handled

        if remaining:
            return super(WorkshopOrder, remaining).action_send_to_workshop()

        return True

    def action_receive_outputs(self):
        res = super().action_receive_outputs()
        self._sale_workshop_assign_outputs_to_sale(manual=False)
        return res

    def action_done(self):
        res = super().action_done()
        self._sale_workshop_assign_outputs_to_sale(manual=False)
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

    def _sale_workshop_get_sale_line_rounding(self, sale_line):
        if hasattr(sale_line, '_stone_workshop_get_line_uom'):
            uom = sale_line._stone_workshop_get_line_uom()
            if uom and uom.rounding:
                return uom.rounding

        if sale_line.product_id and sale_line.product_id.uom_id and sale_line.product_id.uom_id.rounding:
            return sale_line.product_id.uom_id.rounding

        return 0.00001

    def _sale_workshop_assign_outputs_to_sale(self, manual=False):
        """
        Asigna lotes finales a la línea de venta de forma idempotente.

        Antes se hacía:
            breakdown[lot] = breakdown.get(lot, 0) + qty_out

        Eso duplicaba cantidades si action_receive_outputs y luego
        action_assign_outputs_to_sale se ejecutaban sobre las mismas salidas.

        Ahora para los lotes producidos por la OT se fija el valor real
        producido por lote:
            breakdown[lot] = sum(qty_out de esa OT y ese lote)
        """
        assigned_any = False

        for order in self:
            sale_line = order.sale_line_id

            if not sale_line or not sale_line.exists() or not order.sale_order_id:
                continue

            output_lines = order.output_line_ids.filtered(
                lambda o:
                    o.state in ('produced', 'received')
                    and o.output_type not in ('scrap', 'rejected')
                    and o.product_id == sale_line.product_id
                    and o.lot_id
                    and (o.qty_out or 0.0) > 0.0
            )

            if not output_lines:
                continue

            current_lot_ids = set(sale_line.lot_ids.ids) if hasattr(sale_line, 'lot_ids') else set()
            output_lot_ids = set(output_lines.mapped('lot_id').ids)
            target_lot_ids = list(current_lot_ids | output_lot_ids)

            output_qty_by_lot = {}
            for output in output_lines:
                key = str(output.lot_id.id)
                output_qty_by_lot[key] = output_qty_by_lot.get(key, 0.0) + (output.qty_out or 0.0)

            vals = {}
            needs_write = False

            if 'lot_ids' in sale_line._fields and output_lot_ids - current_lot_ids:
                vals['lot_ids'] = [(6, 0, target_lot_ids)]
                needs_write = True

            if 'x_lot_breakdown_json' in sale_line._fields:
                current_breakdown = (
                    sale_line._stone_workshop_parse_breakdown()
                    if hasattr(sale_line, '_stone_workshop_parse_breakdown')
                    else {}
                )
                new_breakdown = dict(current_breakdown or {})
                rounding = order._sale_workshop_get_sale_line_rounding(sale_line)

                breakdown_changed = False

                for key, qty in output_qty_by_lot.items():
                    current_qty = float(current_breakdown.get(key, 0.0) or 0.0)
                    target_qty = float(qty or 0.0)

                    if float_compare(
                        current_qty,
                        target_qty,
                        precision_rounding=rounding,
                    ) != 0:
                        breakdown_changed = True

                    new_breakdown[key] = target_qty

                if breakdown_changed:
                    vals['x_lot_breakdown_json'] = new_breakdown
                    needs_write = True

            if needs_write and vals:
                sale_line.with_context(
                    skip_stone_workshop_product_defaults=True,
                    skip_sale_workshop_reservation=True,
                ).write(vals)

                if hasattr(sale_line, '_sync_lots_to_picking_moves'):
                    sale_line._sync_lots_to_picking_moves()

                order.message_post(body=_(
                    'Se asignaron %(count)s lote(s) finales al pedido %(sale)s.'
                ) % {
                    'count': len(output_lot_ids),
                    'sale': order.sale_order_id.name,
                })

                order.sale_order_id.message_post(body=_(
                    'Taller %(workshop)s terminó producto final para la línea %(line)s. '
                    'Lotes asignados: %(lots)s.'
                ) % {
                    'workshop': order.name,
                    'line': sale_line.product_id.display_name,
                    'lots': ', '.join(output_lines.mapped('lot_id.name')),
                })

                assigned_any = True
            else:
                assigned_any = True
                _logger.info(
                    '[STONE WORKSHOP SALE] Salidas de %s ya estaban asignadas a la línea %s. Sin cambios.',
                    order.name,
                    sale_line.id,
                )

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

    def _check_stock_availability(self):
        sale_linked_lines = self.filtered(
            lambda l:
                l.order_id
                and l.order_id.sale_order_id
                and l.order_id.sale_workshop_reservation_picking_id
        )

        normal_lines = self - sale_linked_lines

        if normal_lines:
            try:
                super(WorkshopInputLine, normal_lines)._check_stock_availability()
            except AttributeError:
                pass

        for line in sale_linked_lines:
            line.order_id._sale_workshop_assert_input_line_effective_available(line)

        return True

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