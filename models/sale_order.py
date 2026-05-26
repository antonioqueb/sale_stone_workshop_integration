# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare

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

    @api.depends('stone_workshop_order_ids.state')
    def _compute_stone_workshop_order_count(self):
        for order in self:
            orders = order.stone_workshop_order_ids
            order.stone_workshop_order_count = len(orders)
            order.stone_workshop_pending_count = len(orders.filtered(lambda o: o.state not in ('done', 'cancel')))

    def action_confirm(self):
        res = super().action_confirm()
        # sale_stone_selection puede devolver una acción de redirección en doble confirmación.
        # Solo intentamos crear OT si la orden quedó realmente confirmada.
        confirmed_orders = self.filtered(lambda o: o.state in ('sale', 'done') and not o.x_is_quote_backup if 'x_is_quote_backup' in o._fields else o.state in ('sale', 'done'))
        if confirmed_orders:
            confirmed_orders._stone_workshop_create_missing_orders()
        return res

    def _stone_workshop_get_workshop_vals(self, line):
        self.ensure_one()
        warehouse = self.warehouse_id or self.env['stock.warehouse'].search([('company_id', '=', self.company_id.id)], limit=1)
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
            'date_planned': self.commitment_date if 'commitment_date' in self._fields else False,
            'notes': notes,
        }
        return vals

    def _stone_workshop_create_missing_orders(self, force_lines=False):
        WorkshopOrder = self.env['workshop.order']
        created_orders = WorkshopOrder
        for order in self:
            if order.state not in ('sale', 'done'):
                continue
            candidate_lines = force_lines.filtered(lambda l: l.order_id == order) if force_lines else order.order_line
            for line in candidate_lines:
                if not line._stone_workshop_needs_supply() and not force_lines:
                    continue
                if not line.stone_workshop_required:
                    continue
                if line.stone_workshop_order_id:
                    created_orders |= line.stone_workshop_order_id
                    continue
                if not line.stone_workshop_base_product_id or not line.stone_workshop_process_id:
                    raise UserError(_(
                        'La línea %(line)s requiere taller, pero no tiene producto base o proceso configurado.'
                    ) % {'line': line.name or line.product_id.display_name})
                vals = order._stone_workshop_get_workshop_vals(line)
                workshop = WorkshopOrder.create(vals)
                line.with_context(skip_stone_workshop_product_defaults=True).write({
                    'stone_workshop_order_id': workshop.id,
                })
                created_orders |= workshop
                body = _(
                    'Se creó la orden de taller <a href="#" data-oe-model="workshop.order" data-oe-id="%(id)s">%(name)s</a> '
                    'para producir <strong>%(final)s</strong> desde <strong>%(base)s</strong>.'
                ) % {
                    'id': workshop.id,
                    'name': workshop.name,
                    'final': line.product_id.display_name,
                    'base': line.stone_workshop_base_product_id.display_name,
                }
                order.message_post(body=body)
                workshop.message_post(body=_('Origen comercial: %s, línea %s.') % (order.name, line.display_name))
                _logger.info(
                    '[STONE WORKSHOP SALE] Created workshop %s for sale %s line %s',
                    workshop.name, order.name, line.id,
                )
        return created_orders

    def action_create_stone_workshop_orders(self):
        created = self._stone_workshop_create_missing_orders()
        if not created:
            raise UserError(_(
                'No se creó ninguna orden de taller. Verifica que las líneas estén configuradas '
                'como producto final con taller y que el disparador aplique.'
            ))
        return self.action_view_stone_workshop_orders()

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
