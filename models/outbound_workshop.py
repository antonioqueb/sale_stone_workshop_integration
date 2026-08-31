# -*- coding: utf-8 -*-
"""Columna "A taller" en el Tablero de Salidas.

EL HUECO QUE CIERRA
-------------------
El vendedor crea la orden de taller desde su venta, pero NO entra a taller
ni puede tocar la OT. El material sigue físicamente en el almacén: la OT
solo dejó un traslado interno RESERVADO (almacén → ubicación de taller) con
las placas exactas. Nadie lo validaba porque nadie se enteraba.

Aquí ese traslado pendiente sale en el tablero que Logística ya usa todos
los días, junto a los pick tickets y las remisiones. Lo entregan desde ahí
y el material queda contablemente en taller.

POR QUÉ NO SE INVENTA UN DOCUMENTO
----------------------------------
El traslado interno YA es el documento real: tiene folio, las placas
reservadas y el movimiento de inventario. Crear encima un documento de
entrega duplicaría las líneas y daría dos folios para el mismo hecho
físico. El tablero trabaja el traslado tal cual.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

# Estados de traslado que significan "todavía no llegó a taller"
_PENDIENTE = ('draft', 'waiting', 'confirmed', 'assigned')


class SaleDeliveryLiveMap(models.TransientModel):
    # TransientModel, NO Model: el tablero de sale_delivery_wizard es
    # transitorio (solo expone métodos, no guarda filas). Heredarlo como
    # models.Model lo convierte en persistente y Odoo se niega a cargar el
    # registro entero — la base no arranca.
    _inherit = 'sale.delivery.live.map'

    @api.model
    def _som_workshop_pending_deliveries(self):
        """OTs en borrador cuyo material sigue sin entregarse a taller."""
        Workshop = self.env['workshop.order'].sudo()
        if 'sale_workshop_reservation_picking_id' not in Workshop._fields:
            return Workshop.browse()
        return Workshop.search([
            ('state', '=', 'draft'),
            ('sale_workshop_reservation_picking_id', '!=', False),
            ('sale_workshop_reservation_picking_id.state', 'in', _PENDIENTE),
            # Tablero (sudo): compañías activas del usuario.
            ('company_id', 'in', self.env.companies.ids),
        ], order='create_date asc')

    @api.model
    def get_outbound_dashboard_data(self):
        data = super().get_outbound_dashboard_data()

        hoy = fields.Date.context_today(self)
        tarjetas = []
        for wo in self._som_workshop_pending_deliveries():
            picking = wo.sale_workshop_reservation_picking_id
            order = wo.sale_order_id

            # Materiales con SU unidad: no todo es m², también llegan
            # piezas y formatos (mismo criterio que el resto del tablero).
            mat = {}
            for ml in picking.move_line_ids:
                if not ml.product_id:
                    continue
                uom = ml.product_id.uom_id.name or ''
                clave = (ml.product_id.display_name, uom)
                mat[clave] = mat.get(clave, 0.0) + (ml.quantity or 0.0)
            materiales = sorted(
                ({'product': k[0], 'qty': round(v, 1), 'uom': k[1]}
                 for k, v in mat.items()),
                key=lambda x: -x['qty'],
            )[:20]

            por_uom = {}
            for m in materiales:
                por_uom[m['uom']] = por_uom.get(m['uom'], 0.0) + m['qty']
            etiqueta = ' · '.join(
                '%g %s' % (round(q, 1), u or 'uds')
                for u, q in sorted(por_uom.items(), key=lambda x: -x[1])
            ) or '0'

            creado = wo.create_date and fields.Datetime.context_timestamp(
                self, wo.create_date)
            dias = (hoy - creado.date()).days if creado else 0

            tarjetas.append({
                'id': picking.id,
                'picking': picking.name or '',
                'state': picking.state,
                # 'assigned' = reservado y listo para mover. Cualquier otro
                # estado significa que el material no está disponible.
                'ready': picking.state == 'assigned',
                'workshop': wo.name or '',
                'workshop_id': wo.id,
                'process': wo.process_id.display_name if wo.process_id else '',
                'order': order.name if order else '',
                'order_id': order.id if order else False,
                'partner': order.partner_id.name if order and order.partner_id else '',
                'seller': order.user_id.name if order and order.user_id else '',
                'materials': materiales,
                'qty_label': etiqueta,
                'lines': len(picking.move_line_ids),
                'days': dias,
                'late': dias >= 2,
            })

        data['to_workshop'] = tarjetas
        data.setdefault('kpis', {})
        data['kpis']['to_workshop'] = len(tarjetas)
        data['kpis']['to_workshop_late'] = sum(1 for t in tarjetas if t['late'])
        return data

    @api.model
    def som_deliver_to_workshop(self, picking_id):
        """Entrega el material a taller desde el tablero.

        NO va con sudo a propósito: mover material del almacén a taller es
        una acción REAL de Logística, y el rastro debe decir quién la hizo.
        Si Odoo pide confirmar algo (backorder, cantidades), se devuelve esa
        ventana al cliente en vez de tragársela.
        """
        picking = self.env['stock.picking'].browse(int(picking_id))
        if not picking.exists():
            return {'ok': False, 'error': _('El traslado ya no existe.')}
        if picking.state == 'done':
            return {'ok': True}
        if picking.state == 'cancel':
            return {'ok': False,
                    'error': _('El traslado %s está cancelado.') % picking.name}

        # Odoo 17+ no valida lo que no está marcado como surtido: sin esto
        # button_validate se queja de que no hay nada que mover, aunque las
        # placas estén reservadas. Se marca solo lo que ya tiene cantidad.
        if 'picked' in self.env['stock.move']._fields:
            con_cantidad = picking.move_ids.filtered(
                lambda m: m.state not in ('done', 'cancel') and m.quantity)
            if con_cantidad:
                con_cantidad.picked = True

        res = picking.button_validate()
        if isinstance(res, dict) and res.get('type'):
            # Wizard de Odoo (backorder / transferencia inmediata): que lo
            # resuelva el usuario, no lo adivinemos aquí.
            return {'ok': False, 'action': res}
        return {'ok': True}
