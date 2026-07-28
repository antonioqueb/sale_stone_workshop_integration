# -*- coding: utf-8 -*-
import logging

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)

ACTIVE_WORKSHOP_STATES = (
    'in_workshop',
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
    sale_user_id = fields.Many2one(
        related='sale_order_id.user_id',
        string='Vendedor',
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

    stone_workshop_chain_sequence = fields.Integer(
        string='Paso de la cadena',
        default=1,
        copy=False,
        help='Posición de esta orden dentro de la cadena de procesos de la venta.',
    )
    stone_workshop_chain_prev_order_id = fields.Many2one(
        'workshop.order',
        string='Paso anterior',
        copy=False,
        ondelete='set null',
        help='Orden de taller cuyo resultado alimenta a esta orden.',
    )
    stone_workshop_chain_next_order_id = fields.Many2one(
        'workshop.order',
        string='Paso siguiente',
        copy=False,
        ondelete='set null',
        help='Orden de taller que consume el resultado de esta orden.',
    )
    stone_workshop_process_line_id = fields.Many2one(
        'sale.stone.workshop.process.line',
        string='Proceso adicional de venta',
        copy=False,
        ondelete='set null',
    )

    @api.depends('sale_workshop_reservation_picking_id.state')
    def _compute_sale_workshop_reserved(self):
        for order in self:
            picking = order.sale_workshop_reservation_picking_id
            order.sale_workshop_reserved = bool(
                picking and picking.state not in ('cancel', 'done')
            )

    def write(self, vals):
        res = super().write(vals)
        # El estado de asignación de la línea de venta depende de TODA la
        # cadena de OTs, pero los @api.depends solo alcanzan un nivel de
        # stone_workshop_chain_next_order_id. Cuando cambia el estado de
        # cualquier paso (p. ej. el 2º o 3º de la cadena), se encola la
        # recomputación explícitamente.
        if 'state' in vals:
            lines = self.mapped('sale_line_id').exists()
            if lines:
                self.env.add_to_compute(
                    lines._fields['stone_workshop_assignment_state'],
                    lines,
                )
        return res

    # ------------------------------------------------------------------
    # Panel del taller: enriquecer la tarjeta con datos comerciales
    # ------------------------------------------------------------------

    def _workshop_board_payload_extend(self, data):
        data = super()._workshop_board_payload_extend(data)
        self.ensure_one()
        data.update({
            'sale_order': self.sale_order_id.name or '',
            'sale_partner': self.sale_partner_id.display_name if self.sale_partner_id else '',
            'sale_user': self.sale_user_id.name if self.sale_user_id else '',
            'sale_product': self.sale_product_id.display_name if self.sale_product_id else '',
            'sale_requested_qty': self.sale_requested_qty or 0.0,
        })
        return data

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

    def action_view_chain_prev_order(self):
        self.ensure_one()
        if not self.stone_workshop_chain_prev_order_id:
            raise UserError(_('Esta orden no tiene un paso anterior en la cadena.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Paso anterior'),
            'res_model': 'workshop.order',
            'view_mode': 'form',
            'res_id': self.stone_workshop_chain_prev_order_id.id,
            'target': 'current',
        }

    def action_view_chain_next_order(self):
        self.ensure_one()
        if not self.stone_workshop_chain_next_order_id:
            raise UserError(_('Esta orden no tiene un paso siguiente en la cadena.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Siguiente proceso'),
            'res_model': 'workshop.order',
            'view_mode': 'form',
            'res_id': self.stone_workshop_chain_next_order_id.id,
            'target': 'current',
        }

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
            'skip_duplicate_lot_validation': True,
            'skip_hold_validation': True,
        }

    def _sale_workshop_reservation_context(self):
        """
        Contexto exclusivo para crear reservas exactas de placas base.

        Evita que stock_whole_lot_removal u otra estrategia automática intente
        reservar lotes antes de que esta integración cree sus move lines exactas.
        """
        ctx = dict(self._sale_workshop_stock_context())
        ctx.update({
            'skip_whole_lot_no_assign': True,
        })
        return ctx

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
            # Liberar reservas del quant antes de cancelar, para no dejar
            # reserved_quantity huérfano cuando los overrides de unlink
            # corren bajo contexto bypass.
            picking.move_ids._do_unreserve()
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

    def _sale_workshop_find_stale_reservation_pickings(self, input_lines=False):
        """
        Busca reservas de taller obsoletas del mismo flujo comercial.

        Caso que corrige:
            total=6.07
            reserved=12.14
            own_reserved=6.07

        Eso indica que existe otra reserva activa de 6.07 para el mismo lote.
        Si esa otra reserva es una reserva de taller anterior sin dueño activo,
        se debe cancelar antes de validar.
        """
        self.ensure_one()

        if not self.sale_order_id:
            return self.env['stock.picking']

        active_input_lines = input_lines or self._sale_workshop_input_lines_to_reserve()
        lot_ids = active_input_lines.mapped('lot_id').ids
        product_ids = active_input_lines.mapped('product_id').ids

        if not lot_ids or not product_ids:
            return self.env['stock.picking']

        # `=like` con "S00012 / %": el origen de estas reservas siempre es
        # "<pedido> / <OT> - Reserva taller". Un ilike simple del nombre del
        # pedido también matcheaba prefijos (S00012 vs S000123) y podía
        # cancelar reservas de otra venta como si fueran obsoletas.
        domain = [
            ('state', 'not in', ('done', 'cancel')),
            ('origin', '=like', '%s / %%' % self.sale_order_id.name),
            ('origin', 'ilike', 'Reserva taller'),
            ('move_ids.move_line_ids.lot_id', 'in', lot_ids),
            ('move_ids.move_line_ids.product_id', 'in', product_ids),
        ]

        pickings = self.env['stock.picking'].search(domain)

        current_picking = self.sale_workshop_reservation_picking_id
        if current_picking:
            pickings -= current_picking

        if not pickings:
            return pickings

        owner_orders = self.env['workshop.order'].search([
            ('sale_workshop_reservation_picking_id', 'in', pickings.ids),
            ('state', 'not in', ('done', 'cancel')),
        ])

        owned_by_other_active_order = owner_orders.filtered(lambda o: o != self)
        protected_pickings = owned_by_other_active_order.mapped('sale_workshop_reservation_picking_id')

        stale_pickings = pickings - protected_pickings

        if protected_pickings:
            _logger.warning(
                '[STONE WORKSHOP SALE] No se cancelan reservas de taller protegidas por otra OT activa. '
                'Orden=%s Pickings=%s Owners=%s',
                self.name,
                protected_pickings.mapped('name'),
                owned_by_other_active_order.mapped('name'),
            )

        return stale_pickings

    def _sale_workshop_cleanup_stale_reservations(self, input_lines=False):
        """
        Cancela reservas de taller obsoletas del mismo pedido/lote antes de validar disponibilidad.
        No cancela reservas protegidas por otra OT activa.
        """
        self.ensure_one()

        stale_pickings = self._sale_workshop_find_stale_reservation_pickings(input_lines=input_lines)

        if not stale_pickings:
            return True

        _logger.warning(
            '[STONE WORKSHOP SALE] Cancelando reservas obsoletas antes de validar %s: %s',
            self.name,
            stale_pickings.mapped('name'),
        )

        for picking in stale_pickings:
            if picking.state not in ('done', 'cancel'):
                # Liberar el quant antes de cancelar para no dejar reserva huérfana.
                picking.move_ids._do_unreserve()
                picking.with_context(**self._sale_workshop_stock_context()).action_cancel()

        return True

    def _sale_workshop_move_line_reserved_qty(self, move_line):
        qty = 0.0

        if 'quantity' in move_line._fields:
            qty = move_line.quantity or 0.0
        elif 'reserved_uom_qty' in move_line._fields:
            qty = move_line.reserved_uom_qty or 0.0
        elif 'qty_done' in move_line._fields:
            qty = move_line.qty_done or 0.0

        if move_line.product_uom_id and move_line.product_id and move_line.product_id.uom_id:
            qty = move_line.product_uom_id._compute_quantity(
                qty,
                move_line.product_id.uom_id,
                rounding_method='HALF-UP',
            )

        return qty

    def _sale_workshop_expected_reserved_qty_for_quant(self, quant):
        """
        Calcula cuánto debería estar reservado según stock.move.line activas.

        Si stock.quant.reserved_quantity es mayor a esto, existe reserva huérfana.
        Si es menor, hay move lines activas que no se reflejaron correctamente.
        """
        StockMoveLine = self.env['stock.move.line'].sudo()

        domain = [
            ('product_id', '=', quant.product_id.id),
            ('location_id', '=', quant.location_id.id),
            ('move_id.state', 'not in', ('done', 'cancel')),
        ]

        if quant.lot_id:
            domain.append(('lot_id', '=', quant.lot_id.id))
        else:
            domain.append(('lot_id', '=', False))

        if 'package_id' in StockMoveLine._fields:
            if quant.package_id:
                domain.append(('package_id', '=', quant.package_id.id))
            else:
                domain.append(('package_id', '=', False))

        if 'owner_id' in StockMoveLine._fields:
            if quant.owner_id:
                domain.append(('owner_id', '=', quant.owner_id.id))
            else:
                domain.append(('owner_id', '=', False))

        qty = 0.0
        for move_line in StockMoveLine.search(domain):
            qty += self._sale_workshop_move_line_reserved_qty(move_line)

        return qty

    def _sale_workshop_reconcile_reserved_quants_for_input_lines(self, input_lines=False):
        """
        Repara stock.quant.reserved_quantity contra las move lines reales.

        Corrige casos como:
            total=6.17
            reserved=12.34
            own_reserved=6.17

        donde el quant quedó duplicado aunque solo existe una reserva real activa.
        """
        self.ensure_one()

        Quant = self.env['stock.quant'].sudo()
        input_lines = input_lines or self._sale_workshop_input_lines_to_reserve()

        seen_quant_ids = set()

        for input_line in input_lines:
            if not input_line.product_id or not input_line.lot_id:
                continue

            location = input_line.location_id or self.location_src_id
            if not location:
                continue

            domain = [
                ('product_id', '=', input_line.product_id.id),
                ('lot_id', '=', input_line.lot_id.id),
                ('location_id', 'child_of', location.id),
            ]

            for quant in Quant.search(domain):
                if quant.id in seen_quant_ids:
                    continue

                seen_quant_ids.add(quant.id)

                current_reserved = quant.reserved_quantity or 0.0
                expected_reserved = self._sale_workshop_expected_reserved_qty_for_quant(quant)

                rounding = (
                    quant.product_id.uom_id.rounding
                    if quant.product_id and quant.product_id.uom_id
                    else 0.00001
                )

                if float_compare(
                    current_reserved,
                    expected_reserved,
                    precision_rounding=rounding,
                ) != 0:
                    _logger.warning(
                        '[STONE WORKSHOP SALE STOCK] Corrigiendo reserved_quantity inconsistente. '
                        'order=%s product=%s lot=%s location=%s current=%s expected=%s',
                        self.name,
                        quant.product_id.display_name,
                        quant.lot_id.name if quant.lot_id else '',
                        quant.location_id.display_name,
                        current_reserved,
                        expected_reserved,
                    )

                    quant.write({
                        'reserved_quantity': max(expected_reserved, 0.0),
                    })

        return True

    def _sale_workshop_create_reservation_picking(self, input_lines):
        """
        Crea el picking interno de reserva para placas base seleccionadas desde venta.

        Este flujo evita que módulos externos de reserva por lote completo creen
        líneas automáticas que choquen con las líneas exactas seleccionadas desde
        el selector de taller.

        Punto crítico:
        Al confirmar los moves, stock_whole_lot_removal puede crear líneas
        automáticas que reservan lotes. Esas líneas deben liberarse con
        _do_unreserve() (no con unlink bajo contexto bypass), porque el unlink
        bypasseado NO decrementa reserved_quantity del quant y deja reserva
        huérfana que se DUPLICA cuando la línea exacta vuelve a reservar el
        mismo lote.

        Reserva exacta:
        Se crea UNA sola move line exacta por lote con `quantity` = qty_in. Al
        crearse sobre un move ya confirmado, Odoo reserva el quant una sola vez.
        NO se llama a stock.quant._update_reserved_quantity manualmente: hacerlo
        duplicaba reserved_quantity (current = 2×qty) y obligaba al reconcile a
        corregirlo en cada OT. El reconcile se mantiene como red de seguridad.
        """
        self.ensure_one()

        if not input_lines:
            return False

        self._ensure_default_locations()

        if not self.location_src_id or not self.location_workshop_id:
            raise UserError(_('Define ubicación origen y ubicación taller antes de reservar placas.'))

        self._sale_workshop_cleanup_stale_reservations(input_lines=input_lines)
        self._sale_workshop_reconcile_reserved_quants_for_input_lines(input_lines=input_lines)

        picking_type = self._get_internal_picking_type()
        bypass_ctx = self._sale_workshop_stock_context()
        reservation_ctx = self._sale_workshop_reservation_context()

        origin = '%s - Reserva taller' % (self.name or '')
        if self.sale_order_id:
            origin = '%s / %s - Reserva taller' % (self.sale_order_id.name, self.name)

        picking = self.env['stock.picking'].with_context(**reservation_ctx).create({
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

            move = StockMove.with_context(**reservation_ctx).create(move_vals)
            moves |= move
            move_specs.append((move, line, source_location))

        if moves:
            try:
                moves.with_context(**reservation_ctx)._action_confirm(merge=False)
            except TypeError:
                moves.with_context(**reservation_ctx)._action_confirm()

        # CLAVE: liberar de verdad las reservas automáticas.
        # No basta unlink() de las move lines: bajo el contexto bypass los
        # overrides de stock_whole_lot_removal / stock_lot_dimensions no
        # decrementan reserved_quantity del quant, dejando reserva huérfana
        # que se DUPLICA cuando la línea exacta vuelve a reservar el mismo lote.
        # _do_unreserve() sí libera el quant correctamente.
        auto_lines = moves.mapped('move_line_ids')
        if auto_lines:
            _logger.info(
                '[STONE WORKSHOP SALE] Liberando %s reserva(s) automática(s) del picking %s antes de crear reserva exacta.',
                len(auto_lines),
                picking.name,
            )
        moves._do_unreserve()
        self._sale_workshop_reconcile_reserved_quants_for_input_lines(input_lines=input_lines)

        created_move_lines = self.env['stock.move.line']

        for move, line, source_location in move_specs:
            # Defensa: si algún hook volvió a reservar, liberar antes de la línea exacta.
            if move.move_line_ids:
                move._do_unreserve()

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

            qty_to_reserve = line.qty_in or 0.0

            # NO reservar manualmente el quant: crear la move line con `quantity`
            # sobre un move ya confirmado reserva el quant una sola vez. Reservar
            # manualmente aquí (stock.quant._update_reserved_quantity) duplicaba
            # reserved_quantity (current = 2×qty) y obligaba al reconcile a
            # corregirlo en cada OT. El reconcile posterior queda como red de
            # seguridad por si algún hook adicional altera la reserva.
            ml_vals.update(self._sale_workshop_move_line_qty_vals(qty_to_reserve))

            move_line = StockMoveLine.with_context(**reservation_ctx).create(ml_vals)
            created_move_lines |= move_line

        self._sale_workshop_reconcile_reserved_quants_for_input_lines(input_lines=input_lines)

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
            if order.state in ('in_workshop', 'done', 'cancel'):
                continue

            existing = order.sale_workshop_reservation_picking_id
            if existing and existing.state == 'done':
                continue

            input_lines = order._sale_workshop_input_lines_to_reserve()

            if not input_lines:
                order._sale_workshop_cancel_input_reservation(reset_lines=True)
                continue

            order._sale_workshop_cleanup_stale_reservations(input_lines=input_lines)
            order._sale_workshop_cancel_input_reservation(reset_lines=False)
            order._sale_workshop_create_reservation_picking(input_lines)

        return True

    # ------------------------------------------------------------------
    # Stock validation helpers
    # ------------------------------------------------------------------

    def _sale_workshop_reserved_qty_for_lot(self, product, lot, location=False):
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

    def _sale_workshop_related_reserved_qty_for_lot(self, product, lot, location=False):
        """
        Cantidad reservada compatible con esta OT.

        Incluye:
        - Picking de reserva actual.
        - Reservas de taller del mismo pedido que no tienen otra OT activa como dueña.

        No incluye:
        - Reservas de otra OT activa.
        - Reservas de entregas o documentos comerciales ajenos.
        """
        self.ensure_one()

        current_qty = self._sale_workshop_reserved_qty_for_lot(product, lot, location=location)

        stale_pickings = self._sale_workshop_find_stale_reservation_pickings()
        if not stale_pickings:
            return current_qty

        location_ids = False
        if location:
            location_ids = set(
                self.env['stock.location'].search([
                    ('id', 'child_of', location.id),
                ]).ids
            )

        stale_qty = 0.0
        for ml in stale_pickings.mapped('move_ids.move_line_ids').filtered(
            lambda l: l.product_id == product and l.lot_id == lot
        ):
            if location_ids and ml.location_id.id not in location_ids:
                continue

            if 'quantity' in ml._fields:
                stale_qty += ml.quantity or 0.0
            elif 'reserved_uom_qty' in ml._fields:
                stale_qty += ml.reserved_uom_qty or 0.0
            elif 'qty_done' in ml._fields:
                stale_qty += ml.qty_done or 0.0

        return current_qty + stale_qty

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

        # Limpia reservas obsoletas antes de calcular, porque reserved_quantity
        # puede venir inflado por pickings anteriores del mismo flujo.
        self._sale_workshop_cleanup_stale_reservations(input_lines=input_line)
        self._sale_workshop_reconcile_reserved_quants_for_input_lines(input_lines=input_line)

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

        self._sale_workshop_cleanup_stale_reservations(input_lines=input_lines)

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
                        '[STONE WORKSHOP SALE] Falso negativo de stock en %s: los '
                        'insumos están cubiertos por su propia reserva %s. Se '
                        're-validan las demás reglas sin el chequeo de cantidad. '
                        'Error original: %s',
                        order.name,
                        order.sale_workshop_reservation_picking_id.name,
                        str(error),
                    )
                    # Antes se hacía `continue` y se perdían las validaciones
                    # que venían DESPUÉS del punto que lanzó el error (lotes
                    # duplicados, conflicto con otra OT activa, salidas). Se
                    # re-ejecuta todo saltando solo el chequeo de cantidad,
                    # que ya está cubierto por la reserva propia.
                    try:
                        super(
                            WorkshopOrder,
                            order.with_context(workshop_skip_qty_check=True),
                        )._validate_business_rules()
                    except UserError as retry_error:
                        retry_message = str(retry_error).lower()
                        if any(term in retry_message for term in stock_error_terms):
                            # Error de stock de otro validador externo: se
                            # conserva la válvula de seguridad original.
                            _logger.warning(
                                '[STONE WORKSHOP SALE] Re-validación de %s aún '
                                'reporta stock; se ignora por reserva propia: %s',
                                order.name,
                                str(retry_error),
                            )
                            continue
                        raise
                    continue

                raise

            return True

    # ------------------------------------------------------------------
    # Workshop flow hooks
    # ------------------------------------------------------------------

    def action_confirm_workshop(self):
        """Override: reutiliza el picking de reserva pre-creado para venta.

        Si la orden viene amarrada a una venta y ya existe un picking de reserva
        no validado, lo usamos como picking de consumo en vez de crear uno
        nuevo. El resto del flujo (auto-generar salidas, validar reglas, pasar
        a `in_workshop`) replica al padre.
        """
        # Candado de cadena: un paso encadenado no puede arrancar hasta que el
        # anterior declare su resultado — su material ES ese resultado.
        for order in self:
            if order.state != 'draft':
                continue
            prev = order.stone_workshop_chain_prev_order_id
            if prev and prev.state != 'done':
                state_labels = dict(prev._fields['state'].selection)
                raise UserError(_(
                    'No puedes enviar %(order)s a taller: es el paso %(seq)s de una '
                    'cadena y el paso anterior %(prev)s (%(state)s) aún no declara su '
                    'resultado. Sus entradas serán exactamente el material que ese '
                    'paso produzca.'
                ) % {
                    'order': order.name,
                    'seq': order.stone_workshop_chain_sequence or 2,
                    'prev': prev.name,
                    'state': state_labels.get(prev.state, prev.state),
                })

        handled = self.env['workshop.order']

        for order in self:
            picking = order.sale_workshop_reservation_picking_id
            pending_inputs = order._sale_workshop_input_lines_to_reserve()

            if not order.sale_order_id or not picking or picking.state in ('cancel', 'done') or not pending_inputs:
                continue

            if order.state != 'draft':
                raise UserError(_('Solo puedes confirmar al taller órdenes en borrador.'))

            order._sale_workshop_cleanup_stale_reservations(input_lines=pending_inputs)
            for input_line in pending_inputs:
                order._sale_workshop_assert_input_line_effective_available(input_line)

            if not order._get_active_output_lines():
                order._auto_generate_outputs()
            order._validate_business_rules()

            order.with_context(**order._sale_workshop_stock_context())._validate_picking(picking)
            order.consume_picking_ids = [(4, picking.id)]

            pending_inputs.with_context(skip_sale_workshop_reservation=True).write({
                'state': 'in_progress',
                'is_consumed': True,
                'consume_picking_id': picking.id,
            })

            order.write({
                'state': 'in_workshop',
                'date_start': order.date_start or fields.Datetime.now(),
            })
            # Igual que el flujo base: el cronómetro arranca al enviar a
            # taller. Sin esto, las OTs de venta nunca registraban sesiones de
            # trabajo (sin avance por tiempo y sin regla de 24 h de pausa).
            order._start_work_session()
            order._sale_workshop_sync_selection_states()

            order.message_post(
                body=_('Material reservado enviado a taller con el picking %s.') % picking.name
            )

            handled |= order

        remaining = self - handled

        result = True
        if remaining:
            result = super(WorkshopOrder, remaining).action_confirm_workshop()

        # Etapas 2+ de una cadena: ticket de corrida automático — el material
        # ya está en piso en el taller (es la salida del paso anterior).
        self._stone_workshop_auto_ticket_chain_stages()

        return result

    def _stone_workshop_auto_ticket_chain_stages(self):
        """Genera automáticamente el ticket de taller (con TODAS las placas)
        para órdenes que son paso 2+ de una cadena, al quedar en taller.

        En esas etapas el material no se surte de almacén: ya está en piso
        porque lo produjo el paso anterior. El operador recibe su ticket
        listo para imprimir sin pasar por el selector. Nunca debe tumbar la
        confirmación: cualquier falla solo se registra en log y chatter."""
        for order in self:
            if order.state != 'in_workshop':
                continue
            prev = order.stone_workshop_chain_prev_order_id
            if not prev:
                continue
            if not hasattr(order, '_workshop_auto_ticket_all_inputs'):
                continue
            try:
                ticket = order._workshop_auto_ticket_all_inputs(notes=_(
                    'Generado automáticamente: material ya en piso en el '
                    'taller (paso %(seq)s de la cadena, producido por '
                    '%(prev)s).'
                ) % {
                    'seq': order.stone_workshop_chain_sequence or 2,
                    'prev': prev.name,
                })
            except Exception:
                _logger.exception(
                    '[CADENA TALLER] No se pudo generar el ticket automático '
                    'de %s; puede generarse manualmente con el selector.',
                    order.name,
                )
                order.message_post(body=_(
                    'No se pudo generar el ticket automático de esta etapa; '
                    'génera el ticket manualmente con "Generar ticket".'
                ))
                continue
            if ticket:
                order.message_post(body=_(
                    'Ticket %(ticket)s generado automáticamente con todas '
                    'las placas (%(count)s): el material ya está en el '
                    'taller, producido por el paso anterior %(prev)s.'
                ) % {
                    'ticket': ticket.name,
                    'count': len(ticket.line_ids),
                    'prev': prev.name,
                })

    def action_declare_result(self):
        # La validación base del picking de entrada de salidas dispara
        # _trigger_assign(), que re-reserva automáticamente movimientos de
        # entregas pendientes del mismo producto. Esa reasignación automática
        # puede chocar con el guardia de duplicidad de lote de
        # stock_lot_dimensions durante la cascada (no es una selección manual).
        # Se corre con el contexto de bypass que ya usa el módulo para sus
        # propios pickings, evitando que la cascada interna aborte la recepción.
        res = super(
            WorkshopOrder,
            self.with_context(**self._sale_workshop_stock_context()),
        ).action_declare_result()
        self._sale_workshop_assign_outputs_to_sale(manual=False)
        self._sale_workshop_release_unused_selections()
        self._stone_workshop_feed_next_chain_orders()
        return res

    def _sale_workshop_release_unused_selections(self):
        """Libera las selecciones de placas devueltas como no usadas.

        Al declarar el resultado, las placas nunca registradas en bitácora se
        devuelven al stock origen con su línea de entrada en 'pending'. Si la
        selección comercial quedara activa ('selected'), la placa seguiría
        contando como comprometida en los selectores de venta y taller aunque
        físicamente esté disponible. Aquí se cancela esa selección para
        liberarla de verdad.
        """
        for order in self:
            stale = order.sale_workshop_input_selection_ids.filtered(
                lambda s:
                    s.state != 'cancelled'
                    and s.workshop_input_line_id
                    and not s.workshop_input_line_id.is_consumed
                    and s.workshop_input_line_id.state == 'pending'
            )
            if not stale:
                continue

            lots = ', '.join(stale.mapped('lot_id.name'))
            stale.write({'state': 'cancelled'})
            order.message_post(body=_(
                'Selecciones liberadas por devolución de placas no usadas: %s.'
            ) % lots)

            if order.sale_order_id:
                order.sale_order_id.message_post(body=_(
                    'Taller %(workshop)s devolvió placas no usadas al stock; '
                    'quedaron liberadas para otras ventas: %(lots)s.'
                ) % {'workshop': order.name, 'lots': lots})

        return True

    def _stone_workshop_chain_produced_outputs(self):
        """Salidas útiles declaradas por esta OT (lo que puede consumir el
        siguiente paso de la cadena): producidas/recibidas, con lote y
        cantidad, excluyendo merma y rechazo."""
        self.ensure_one()
        return self.output_line_ids.filtered(
            lambda o:
                o.state in ('produced', 'received')
                and o.output_type not in ('scrap', 'rejected')
                and o.lot_id
                and (o.qty_out or 0.0) > 0.0
        )

    def _stone_workshop_chain_allowed_lots(self):
        """Lotes admisibles como entrada de esta OT encadenada.

        Devuelve None si la OT no es un paso encadenado (sin restricción).
        Para pasos encadenados: exactamente los lotes que produjo el paso
        anterior del producto que este paso consume.
        """
        self.ensure_one()
        prev = self.stone_workshop_chain_prev_order_id
        if not prev:
            return None

        outputs = prev._stone_workshop_chain_produced_outputs()
        if self.input_product_id:
            outputs = outputs.filtered(lambda o: o.product_id == self.input_product_id)
        return outputs.mapped('lot_id')

    def _stone_workshop_feed_next_chain_orders(self):
        """Alimenta la siguiente orden de la cadena con los lotes producidos.

        Tras declarar el resultado de una orden, toma sus salidas finales (no
        merma) cuyo producto coincide con el producto que consume la siguiente
        orden y las crea como líneas de entrada de ésta. Al crearlas sin el
        contexto de bypass, el override de ``workshop.input.line.create`` dispara
        la reserva automática (`_sale_workshop_refresh_input_reservation`).
        """
        WorkshopInput = self.env['workshop.input.line']

        for order in self:
            nxt = order.stone_workshop_chain_next_order_id

            if not nxt or nxt.state != 'draft':
                continue

            if not nxt.input_product_id:
                continue

            # Si la siguiente orden ya tiene entradas activas, no duplicar.
            if nxt.input_line_ids.filtered(lambda l: l.state != 'cancelled'):
                continue

            produced = order._stone_workshop_chain_produced_outputs().filtered(
                lambda o: o.product_id == nxt.input_product_id
            )

            lot_ids = produced.mapped('lot_id').ids
            if not lot_ids:
                _logger.info(
                    '[STONE WORKSHOP SALE] La orden %s no produjo lotes de %s para alimentar %s.',
                    order.name,
                    nxt.input_product_id.display_name,
                    nxt.name,
                )
                # Aviso VISIBLE: antes esto solo quedaba en el log del
                # servidor y el usuario no tenía forma de saber por qué el
                # siguiente paso quedó sin material.
                order.message_post(body=Markup(_(
                    '⚠ El siguiente proceso <strong>%(next)s</strong> NO se alimentó: esta orden '
                    'no produjo lotes de <strong>%(product)s</strong> (el producto que ese paso '
                    'consume). Revisa el producto de salida de esta orden o alimenta el '
                    'siguiente paso manualmente.'
                )) % {
                    'next': nxt.name,
                    'product': nxt.input_product_id.display_name,
                })
                continue

            nxt._ensure_default_locations()

            # La cantidad a ingresar al siguiente paso es EXACTAMENTE la
            # declarada como producida (qty_out / área de la salida), no lo
            # que diga el stock en ese instante. Si el paso 1 declaró 97.5 m²
            # pulidos, el paso 2 recibe 97.5 m², ni más ni menos.
            produced_by_lot = {}
            for output in produced:
                data = produced_by_lot.setdefault(output.lot_id.id, {
                    'qty': 0.0,
                    'area': 0.0,
                    'pieces': 0,
                    'width_cm': output.width_cm or 0.0,
                    'height_cm': output.height_cm or 0.0,
                    'thickness_cm': output.thickness_cm or 0.0,
                })
                data['qty'] += output.qty_out or 0.0
                data['area'] += order._output_line_area(output)
                data['pieces'] += output.pieces or 0

            vals_list = nxt.prepare_input_line_vals_from_lots(
                nxt.input_product_id.id,
                lot_ids,
                location_id=nxt.location_src_id.id if nxt.location_src_id else False,
            )

            for vals in vals_list:
                lot_id = vals.get('lot_id')
                if isinstance(lot_id, (list, tuple)):
                    lot_id = lot_id[0] if lot_id else False
                data = produced_by_lot.get(lot_id)
                if data:
                    vals['qty_in'] = data['qty']
                    vals['area_sqm'] = data['area'] or data['qty']
                    if data['pieces']:
                        vals['pieces'] = data['pieces']
                    for dim in ('width_cm', 'height_cm', 'thickness_cm'):
                        if data[dim]:
                            vals[dim] = data[dim]

                vals['order_id'] = nxt.id
                vals.setdefault(
                    'reserved_origin',
                    nxt.sale_order_id.name if nxt.sale_order_id else (order.name or ''),
                )
                WorkshopInput.with_context(
                    stone_workshop_chain_feeding=True,
                ).create(vals)

            nxt.message_post(
                body=_(
                    'Entradas alimentadas automáticamente desde la orden anterior %(prev)s: %(lots)s.'
                ) % {
                    'prev': order.name,
                    'lots': ', '.join(produced.mapped('lot_id.name')),
                }
            )

            # Rastro en la orden que terminó, con liga al siguiente paso.
            # Markup: message_post escapa strings planos; sin esto la liga se
            # mostraba como código HTML literal en el chatter.
            order.message_post(body=Markup(_(
                'El material producido alimentó automáticamente el siguiente proceso '
                '<a href="#" data-oe-model="workshop.order" data-oe-id="%(id)s">%(name)s</a>: %(lots)s.'
            )) % {
                'id': nxt.id,
                'name': nxt.name,
                'lots': ', '.join(produced.mapped('lot_id.name')),
            })

            if order.sale_order_id:
                order.sale_order_id.message_post(body=_(
                    'Taller: %(prev)s terminó y alimentó el siguiente proceso %(next)s '
                    'con %(count)s lote(s).'
                ) % {
                    'prev': order.name,
                    'next': nxt.name,
                    'count': len(lot_ids),
                })

            _logger.info(
                '[STONE WORKSHOP SALE] Orden %s alimentó %s lote(s) a la siguiente orden %s.',
                order.name,
                len(lot_ids),
                nxt.name,
            )

        return True

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
        assigned_any = False

        for order in self:
            sale_line = order.sale_line_id

            if not sale_line or not sale_line.exists() or not order.sale_order_id:
                continue

            # Paso intermedio de una cadena: su salida es un producto
            # intermedio que alimenta al siguiente paso, NUNCA se asigna a la
            # venta (aunque por configuración el producto coincidiera con el
            # vendido). Solo el último paso asigna.
            if order.stone_workshop_chain_next_order_id:
                if manual:
                    raise UserError(_(
                        'La orden %(order)s es un paso intermedio de la cadena: su salida '
                        'alimenta al siguiente proceso (%(next)s), no se asigna al pedido.'
                    ) % {
                        'order': order.name,
                        'next': order.stone_workshop_chain_next_order_id.name,
                    })
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

    # -------------------------------------------------------------------------
    # Candado de cadena: las entradas de un paso encadenado son EXACTAMENTE
    # el resultado del paso anterior; no se capturan a mano ni por adelantado.
    # -------------------------------------------------------------------------

    def _stone_workshop_assert_chain_input_allowed(self, order, lot_id=False, qty_in=None):
        """Valida una entrada contra las reglas de cadena.

        - Paso anterior sin terminar: NO se pueden capturar entradas (se
          cargan solas al declarar el resultado del paso previo).
        - Paso anterior terminado: solo se admiten lotes producidos por él,
          y nunca más cantidad de la que declaró producida.
        El alimentador automático pasa `stone_workshop_chain_feeding` y queda
        exento (él mismo garantiza lote y cantidad exactos).
        """
        if self.env.context.get('stone_workshop_chain_feeding'):
            return True
        if not order:
            return True

        prev = order.stone_workshop_chain_prev_order_id
        if not prev:
            return True

        state_labels = dict(prev._fields['state'].selection)

        if prev.state != 'done':
            raise UserError(_(
                'La orden %(order)s es el paso %(seq)s de una cadena de procesos: sus '
                'entradas serán EXACTAMENTE el material que produzca el paso anterior '
                '%(prev)s (%(state)s) y se cargan solas cuando ese paso declare su '
                'resultado. No puedes capturarlas manualmente antes.'
            ) % {
                'order': order.name,
                'seq': order.stone_workshop_chain_sequence or 2,
                'prev': prev.name,
                'state': state_labels.get(prev.state, prev.state),
            })

        allowed_lots = order._stone_workshop_chain_allowed_lots()

        lot = False
        if lot_id:
            try:
                lot = self.env['stock.lot'].browse(int(lot_id)).exists()
            except (TypeError, ValueError):
                lot = False

        if lot and allowed_lots is not None and lot not in allowed_lots:
            raise UserError(_(
                'La orden %(order)s solo puede consumir los lotes producidos por el '
                'paso anterior %(prev)s (%(lots)s). El lote %(lot)s no es resultado '
                'de ese paso.'
            ) % {
                'order': order.name,
                'prev': prev.name,
                'lots': ', '.join(allowed_lots.mapped('name')) or _('ninguno'),
                'lot': lot.name,
            })

        if lot and qty_in is not None:
            try:
                qty = float(qty_in or 0.0)
            except (TypeError, ValueError):
                qty = 0.0

            produced_qty = sum(
                prev._stone_workshop_chain_produced_outputs().filtered(
                    lambda o: o.lot_id == lot
                ).mapped('qty_out')
            )
            rounding = (
                lot.product_id.uom_id.rounding
                if lot.product_id and lot.product_id.uom_id
                else 0.00001
            )
            if produced_qty and float_compare(qty, produced_qty, precision_rounding=rounding) > 0:
                raise UserError(_(
                    'La cantidad %(qty)s del lote %(lot)s excede lo que el paso anterior '
                    '%(prev)s declaró producido (%(produced)s). El paso solo puede '
                    'consumir el resultado real del anterior.'
                ) % {
                    'qty': qty,
                    'lot': lot.name,
                    'prev': prev.name,
                    'produced': produced_qty,
                })

        return True

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
        for vals in vals_list:
            order_value = vals.get('order_id')
            if isinstance(order_value, (list, tuple)):
                order_value = order_value[0] if order_value else False
            order = (
                self.env['workshop.order'].browse(int(order_value)).exists()
                if order_value else False
            )
            lot_value = vals.get('lot_id')
            if isinstance(lot_value, (list, tuple)):
                lot_value = lot_value[0] if lot_value else False
            self._stone_workshop_assert_chain_input_allowed(
                order,
                lot_id=lot_value,
                qty_in=vals.get('qty_in'),
            )

        lines = super().create(vals_list)

        if not self.env.context.get('skip_sale_workshop_reservation'):
            lines.mapped('order_id')._sale_workshop_refresh_input_reservation()

        return lines

    def write(self, vals):
        orders = self.mapped('order_id')

        if any(key in vals for key in ('lot_id', 'product_id', 'qty_in', 'order_id')):
            for line in self:
                order_value = vals.get('order_id')
                if isinstance(order_value, (list, tuple)):
                    order_value = order_value[0] if order_value else False
                order = (
                    self.env['workshop.order'].browse(int(order_value)).exists()
                    if order_value else line.order_id
                )
                lot_value = vals.get('lot_id', line.lot_id.id)
                if isinstance(lot_value, (list, tuple)):
                    lot_value = lot_value[0] if lot_value else False
                self._stone_workshop_assert_chain_input_allowed(
                    order,
                    lot_id=lot_value,
                    qty_in=vals.get('qty_in') if 'qty_in' in vals else None,
                )

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