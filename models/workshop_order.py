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
        return res

    def unlink(self):
        orders = self.mapped('order_id')
        res = super().unlink()
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
