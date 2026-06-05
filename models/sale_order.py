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
            # Al confirmar venta SIEMPRE se crea la OT de las líneas elegibles
            # (las que tienen producto base, proceso y requieren taller). Se
            # ignoran los gates de auto_create, trigger=manual y needs_supply
            # porque el usuario explícitamente seleccionó placas y proceso para
            # esa línea — ya no es necesario un botón "Crear OT taller".
            candidate_lines = confirmed_orders._stone_workshop_manual_candidate_lines()
            if candidate_lines:
                confirmed_orders._stone_workshop_create_missing_orders(
                    force_lines=candidate_lines,
                )

        return res

    # -------------------------------------------------------------------------
    # Preparación de valores
    # -------------------------------------------------------------------------

    def _stone_workshop_get_workshop_vals(
        self,
        line,
        input_product=None,
        output_product=None,
        process=None,
        sequence=1,
    ):
        """Prepara los valores de una orden de taller para un paso de la cadena.

        Por defecto usa el proceso/producto base/producto vendido de la línea
        (paso único, comportamiento original). Para cadenas se pasa el producto
        de entrada (intermedio que consume), el de salida (intermedio que produce
        o producto vendido en el último paso), el proceso y la posición.
        """
        self.ensure_one()

        if process is None:
            process = line.stone_workshop_process_id
        if input_product is None:
            input_product = line.stone_workshop_base_product_id
        if output_product is None:
            output_product = line.product_id

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
            '<li>Paso de taller: %(sequence)s</li>'
            '<li>Producto que consume: %(base)s</li>'
            '<li>Producto que produce: %(out)s</li>'
            '<li>Proceso: %(process)s</li>'
            '</ul>'
        ) % {
            'sale': self.name or '',
            'line': line.name or line.product_id.display_name or '',
            'final': line.product_id.display_name or '',
            'sequence': sequence,
            'base': input_product.display_name or '',
            'out': output_product.display_name or '',
            'process': process.display_name or '',
        }

        vals = {
            'sale_order_id': self.id,
            'sale_line_id': line.id,
            'operation_mode': line.stone_workshop_operation_mode or 'slab_finish',
            'process_id': process.id,
            'input_product_id': input_product.id,
            'default_product_out_id': output_product.id,
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
            'stone_workshop_chain_sequence': sequence,
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

                chain_orders = order._stone_workshop_create_chain_for_line(line)

                if not chain_orders:
                    continue

                created_orders |= chain_orders
                first_order = chain_orders[0]

                if len(chain_orders) == 1:
                    body = _(
                        'Se creó la orden de taller '
                        '<a href="#" data-oe-model="workshop.order" data-oe-id="%(id)s">%(name)s</a> '
                        'para producir <strong>%(final)s</strong> desde <strong>%(base)s</strong>.'
                    ) % {
                        'id': first_order.id,
                        'name': first_order.name,
                        'final': line.product_id.display_name,
                        'base': line.stone_workshop_base_product_id.display_name,
                    }
                else:
                    body = _(
                        'Se creó una cadena de %(count)s órdenes de taller para producir '
                        '<strong>%(final)s</strong>: %(chain)s.'
                    ) % {
                        'count': len(chain_orders),
                        'final': line.product_id.display_name,
                        'chain': ' → '.join(chain_orders.mapped('name')),
                    }

                order.message_post(body=body)

                for workshop in chain_orders:
                    workshop.message_post(
                        body=_('Origen comercial: %s, línea %s (paso %s/%s).') % (
                            order.name,
                            line.display_name,
                            workshop.stone_workshop_chain_sequence,
                            len(chain_orders),
                        )
                    )

                _logger.info(
                    '[STONE WORKSHOP SALE] Created workshop chain %s for sale %s line %s',
                    chain_orders.mapped('name'),
                    order.name,
                    line.id,
                )

        return created_orders

    def _stone_workshop_create_chain_for_line(self, line):
        """Crea la cadena de órdenes de taller para una línea de venta.

        Crea una OT por cada paso de ``line._stone_workshop_chain_steps()`` y las
        enlaza (prev/next). El primer paso consume el producto origen (se le
        empujan las placas seleccionadas y se reserva); los pasos intermedios se
        crean en borrador a la espera de que el paso anterior declare su resultado
        y los alimente automáticamente.
        """
        self.ensure_one()

        WorkshopOrder = self.env['workshop.order']
        chain_orders = WorkshopOrder
        prev_order = WorkshopOrder

        steps = line._stone_workshop_chain_steps()

        for step in steps:
            vals = self._stone_workshop_get_workshop_vals(
                line,
                input_product=step['input_product'],
                output_product=step['output_product'],
                process=step['process'],
                sequence=step['sequence'],
            )

            if step['process_line']:
                vals['stone_workshop_process_line_id'] = step['process_line'].id

            workshop = WorkshopOrder.create(vals)

            if prev_order:
                prev_order.write({'stone_workshop_chain_next_order_id': workshop.id})
                workshop.write({'stone_workshop_chain_prev_order_id': prev_order.id})

            if step['sequence'] == 1:
                # Paso principal: vincula la línea y empuja las placas seleccionadas.
                line.with_context(skip_stone_workshop_product_defaults=True).write({
                    'stone_workshop_order_id': workshop.id,
                })
                line._stone_workshop_push_input_selections_to_workshop(workshop)
            elif step['process_line']:
                step['process_line'].write({'workshop_order_id': workshop.id})

            chain_orders |= workshop
            prev_order = workshop

        return chain_orders

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
