# -*- coding: utf-8 -*-
"""OT nacida de una venta (30 sep 2026).

* Las placas elegidas quedan comprometidas y reservadas desde el borrador.
* Confirmar avisa a Logística (actividad) y la OT aparece en Salidas › A taller.
* No se puede iniciar mientras Logística no entregue el material.
* Al entregar, las placas salen del almacén (consumidas), se cierra el aviso y
  la OT queda confirmada lista para iniciar; iniciar arranca el reloj.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.stone_workshop.tests.common import WorkshopCase


@tagged('post_install', '-at_install', 'sale_stone_workshop_integration')
class TestSaleWorkshopLogistics(WorkshopCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'PRUEBA Cliente taller'})
        group = cls.env.ref('sale_delivery_wizard.group_delivery_user')
        cls.logistics_user = cls.env['res.users'].create({
            'name': 'PRUEBA Logística', 'login': 'prueba_logistica_taller',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id, group.id,
                                  cls.env.ref('stock.group_stock_user').id])],
        })
        logistics = cls.env.ref('sale_delivery_auth.group_delivery_logistics', raise_if_not_found=False)
        if logistics:
            cls.logistics_user.write({'group_ids': [(4, logistics.id)]})

    def _sale_order_with_workshop(self, lots_qty):
        so = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.finished.id,
                'product_uom_qty': sum(q for _l, q in lots_qty),
                'price_unit': 100.0,
            })],
        })
        line = so.order_line
        line.with_context(skip_stone_workshop_autosync=True).write({
            'stone_workshop_required': True,
            'stone_workshop_base_product_id': self.slab.id,
            'stone_workshop_process_id': self.p_finish.id,
        })
        order = self.env['workshop.order'].create({
            'process_id': self.p_finish.id,
            'company_id': self.company.id,
            'warehouse_id': self.warehouse.id,
            'input_product_id': self.slab.id,
            'default_product_out_id': self.finished.id,
            'sale_order_id': so.id,
            'sale_line_id': line.id,
            'input_line_ids': [(0, 0, {
                'product_id': self.slab.id, 'lot_id': lot.id, 'qty_in': qty,
                'area_sqm': qty, 'location_id': self.stock_loc.id,
            }) for lot, qty in lots_qty],
        })
        return so, order

    def _activities(self, order):
        return self.env['mail.activity'].search([
            ('res_model', '=', 'workshop.order'), ('res_id', '=', order.id),
            ('summary', '=like', 'Entrega a taller%')])

    def test_01_selected_slabs_are_committed_and_reserved(self):
        lot = self.make_lot('PRB-S01', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        picking = order.sale_workshop_reservation_picking_id
        self.assertTrue(picking, 'La OT de venta deja un traslado de reserva')
        self.assertIn(picking.state, ('assigned', 'confirmed'))
        committed = self.env['stock.quant']._get_committed_lot_ids(self.slab.id)
        self.assertIn(lot.id, committed, 'Placa elegida = comprometida desde borrador')
        order.action_confirm_workshop()
        committed = self.env['stock.quant']._get_committed_lot_ids(self.slab.id)
        self.assertIn(lot.id, committed, 'Sigue comprometida con la OT confirmada')

    def test_02_confirm_notifies_logistics_and_blocks_start(self):
        lot = self.make_lot('PRB-S02', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        picking = order.sale_workshop_reservation_picking_id
        order.action_confirm_workshop()
        self.assertEqual(order.state, 'confirmed')
        self.assertFalse(order.timer_running)
        self.assertNotEqual(picking.state, 'done', 'Confirmar no entrega el material')
        self.assertAlmostEqual(self.qty_at(lot, self.stock_loc), 5.0, places=3)
        acts = self._activities(order)
        self.assertTrue(acts, 'Se avisa a Logística')
        self.assertIn(self.logistics_user, acts.mapped('user_id'))
        order.invalidate_recordset()
        self.assertFalse(order.material_ready)
        self.assertIn(picking.name, order.material_block_reason)
        with self.assertRaisesRegex(UserError, 'Logística'):
            order.action_start_workshop()
        self.assertEqual(order.state, 'confirmed')
        # Reconfirmar/avisar dos veces no duplica.
        order._sale_workshop_notify_logistics_delivery()
        self.assertEqual(len(self._activities(order)), len(acts))

    def test_03_outbound_board_lists_only_confirmed(self):
        lot1 = self.make_lot('PRB-S03', 5.0)
        lot2 = self.make_lot('PRB-S04', 5.0)
        _so1, draft_order = self._sale_order_with_workshop([(lot1, 5.0)])
        _so2, conf_order = self._sale_order_with_workshop([(lot2, 5.0)])
        conf_order.action_confirm_workshop()
        data = self.env['sale.delivery.live.map'].get_outbound_dashboard_data()
        ids = [c['workshop_id'] for c in data.get('to_workshop', [])]
        self.assertIn(conf_order.id, ids)
        self.assertNotIn(draft_order.id, ids, 'Un borrador no compromete a Logística')

    def test_04_logistics_delivers_then_workshop_starts(self):
        lot = self.make_lot('PRB-S05', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        picking = order.sale_workshop_reservation_picking_id
        order.action_confirm_workshop()
        res = self.env['sale.delivery.live.map'].with_user(self.logistics_user).som_deliver_to_workshop(picking.id)
        self.assertTrue(res.get('ok'), res)
        self.assertEqual(picking.state, 'done')
        order.invalidate_recordset()
        self.assertEqual(order.state, 'confirmed', 'Entregar no inicia la OT')
        self.assertTrue(all(order.input_line_ids.mapped('is_consumed')))
        self.assertIn(picking, order.consume_picking_ids)
        self.assertAlmostEqual(self.qty_at(lot, self.stock_loc), 0.0, places=3,
                               msg='La placa entregada ya no está disponible en almacén')
        self.assertFalse(self._activities(order), 'El aviso a Logística se cierra solo')
        self.assertTrue(order.material_ready)
        data = self.env['sale.delivery.live.map'].get_outbound_dashboard_data()
        self.assertNotIn(order.id, [c['workshop_id'] for c in data.get('to_workshop', [])])
        order.action_start_workshop()
        self.assertEqual(order.state, 'in_workshop')
        self.assertTrue(order.timer_running)
        self.assertEqual(len(order.consume_picking_ids), 1, 'No se vuelve a mover el material')

    def test_05_print_pick_from_board(self):
        lot = self.make_lot('PRB-S06', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        order.action_confirm_workshop()
        action = order.action_print_pick_report()
        self.assertEqual(action.get('type'), 'ir.actions.report')

    def test_06_unconfirm_closes_notice(self):
        lot = self.make_lot('PRB-S07', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        order.action_confirm_workshop()
        self.assertTrue(self._activities(order))
        order.action_draft()
        self.assertEqual(order.state, 'draft')
        self.assertFalse(self._activities(order))

    def test_07_cancel_sale_cancels_confirmed_ot_and_releases(self):
        lot = self.make_lot('PRB-S08', 5.0)
        so, order = self._sale_order_with_workshop([(lot, 5.0)])
        order.action_confirm_workshop()
        so._stone_workshop_release_on_cancel()
        self.assertEqual(order.state, 'cancel')
        committed = self.env['stock.quant']._get_committed_lot_ids(self.slab.id)
        self.assertNotIn(lot.id, committed)

    def test_08_validate_reservation_from_inventory_also_applies(self):
        """Si Logística valida el traslado desde Inventario (no desde el tablero)."""
        lot = self.make_lot('PRB-S09', 5.0)
        _so, order = self._sale_order_with_workshop([(lot, 5.0)])
        picking = order.sale_workshop_reservation_picking_id
        order.action_confirm_workshop()
        picking.move_ids.picked = True
        picking.with_context(**order._sale_workshop_stock_context()).button_validate()
        self.assertEqual(picking.state, 'done')
        order.invalidate_recordset()
        self.assertTrue(all(order.input_line_ids.mapped('is_consumed')))
        self.assertFalse(self._activities(order))
        order.action_start_workshop()
        self.assertEqual(order.state, 'in_workshop')
